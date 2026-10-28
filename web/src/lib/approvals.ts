/**
 * One approval module for every window that resolves a one-time confirmation (Hub, Approvals,
 * Import): the API calls, what their failures mean, the state machine of one confirmation, and
 * a store that keeps that state per grant for the whole console session.
 *
 * The server decides; this module only makes sure the console never claims more than the server
 * said:
 * - one request per confirmation at a time: a double click or a held Enter sends one;
 * - a resolved confirmation (approved, rejected, expired, …) never changes back, whatever a later
 *   response says;
 * - the local clock expires nothing: the countdown is for reading, the server's 410 decides;
 * - when the reply to an approve or reject was lost in transit and trying again finds the
 *   confirmation gone, the audit log says what really happened.
 */
import {
  api,
  toFailure,
  type ApprovalItem,
  type ApprovalOutcome,
  type AuditEntryOut,
  type Failure,
  type ImportResultOut,
  type RejectOut,
} from "./api";

export type ApprovalAction = "approve" | "reject";

/** How the console learned an outcome: the reply to its own request, or the audit log. */
export type OutcomeSource = "reply" | "audit";

export type ApprovalState =
  /** Nothing has been done to it here. */
  | { phase: "pending" }
  /** A request is out. `inDoubt`: an earlier one was lost in transit, so the server may already have acted. */
  | { phase: "approving" | "rejecting"; attempt: number; inDoubt: boolean }
  /** Trying again found the confirmation gone, after a reply was lost: reading the audit log. */
  | { phase: "checking"; attempt: number; action: ApprovalAction }
  /**
   * The approved request ran; `outcome.status` says how it went. `by`: who approved it, null for
   * the signed-in user. `elapsedMs`: measured here, null when the audit log told us.
   */
  | {
      phase: "approved";
      outcome: ApprovalOutcome;
      at: number;
      by: string | null;
      elapsedMs: number | null;
      source: OutcomeSource;
    }
  | { phase: "rejected"; at: number; by: string | null; source: OutcomeSource }
  /**
   * `reply`: the server said 410 to an approve. `list`: it stopped listing it after its time ran
   * out. `audit`: after a lost reply, the audit log holds the server's 410 to an approve.
   */
  | { phase: "expired"; at: number; source: "reply" | "list" | "audit" }
  /** The server stopped listing it while it was still untouched here: resolved in another tab or window. */
  | { phase: "elsewhere"; at: number }
  /**
   * The confirmation is gone and the audit log does not say how it ended. `started`: a write
   * started under it but has no result yet; otherwise no write or rejection was recorded at all.
   */
  | { phase: "unconfirmed"; started: boolean; at: number }
  /** The last request failed. `error.retryable`: it was lost in transit, so it may be sent again. */
  | { phase: "failed"; action: ApprovalAction; error: Failure; at: number; inDoubt: boolean };

export type ApprovalPhase = ApprovalState["phase"];

/** What the audit log says became of a confirmation. */
export type AuditVerdict =
  | { kind: "approved"; outcome: ApprovalOutcome; by: string; at: number }
  | { kind: "rejected"; by: string; at: number }
  /** It ran out before it was approved: the server refused an approve with 410. */
  | { kind: "expired"; at: number }
  /** A write started under it; its result is not recorded (yet). */
  | { kind: "started" }
  /** No write and no rejection was recorded under it. */
  | { kind: "unrecorded" };

export type ApprovalEvent =
  | { type: "start"; action: ApprovalAction; attempt: number }
  | { type: "approved"; attempt: number; outcome: ApprovalOutcome; at: number; elapsedMs: number }
  | { type: "rejected"; attempt: number; at: number }
  | { type: "failed"; attempt: number; error: Failure; at: number }
  | { type: "verdict"; attempt: number; verdict: AuditVerdict; at: number }
  /** The server's pending list no longer has it. `overdue`: its time had run out by then. */
  | { type: "departed"; overdue: boolean; at: number };

export const PENDING: ApprovalState = Object.freeze({ phase: "pending" });

/** Code of a failed audit lookup that was not lost in transit (e.g. the role may not read the log). */
export const AUDIT_UNREADABLE = "audit_unreadable";

const RESOLVED: ReadonlySet<ApprovalPhase> = new Set(["approved", "rejected", "expired", "elsewhere", "unconfirmed"]);
const BUSY: ReadonlySet<ApprovalPhase> = new Set(["approving", "rejecting", "checking"]);

/** Over for good: nothing this console receives changes it back. */
export const isResolved = (state: ApprovalState): boolean => RESOLVED.has(state.phase);

/** A request (or the audit lookup after one) is out. */
export const isBusy = (state: ApprovalState): boolean => BUSY.has(state.phase);

/** Approve or reject may be sent: untouched, or the last request was lost in transit. */
export const canAct = (state: ApprovalState): boolean =>
  state.phase === "pending" || (state.phase === "failed" && state.error.retryable);

/** The server's verdict on an approve: this confirmation has expired. */
const isExpiry = (failure: Failure) => failure.kind === "http" && failure.status === 410;

/**
 * The server no longer has the confirmation pending (404), or it is already used (409). After a
 * reply was lost, that may be the lost request's own doing.
 */
const isGone = (failure: Failure) => failure.kind === "http" && (failure.status === 404 || failure.status === 409);

/**
 * One confirmation's transitions. Pure; anything that does not fit the current state is ignored
 * (returns `state` itself): a second start while a request is out, a reply to an older attempt,
 * and every event once the confirmation is resolved.
 */
export function approvalReducer(state: ApprovalState, event: ApprovalEvent): ApprovalState {
  if (isResolved(state)) return state;
  switch (event.type) {
    case "start":
      if (!canAct(state)) return state;
      return {
        phase: event.action === "approve" ? "approving" : "rejecting",
        attempt: event.attempt,
        inDoubt: state.phase === "failed" && state.inDoubt,
      };
    case "approved":
      if (state.phase !== "approving" || state.attempt !== event.attempt) return state;
      return {
        phase: "approved",
        outcome: event.outcome,
        at: event.at,
        by: null,
        elapsedMs: event.elapsedMs,
        source: "reply",
      };
    case "rejected":
      if (state.phase !== "rejecting" || state.attempt !== event.attempt) return state;
      return { phase: "rejected", at: event.at, by: null, source: "reply" };
    case "failed": {
      if (state.phase === "checking") {
        // the audit log could not be read: still in doubt, and it says so
        if (state.attempt !== event.attempt) return state;
        return { phase: "failed", action: state.action, error: event.error, at: event.at, inDoubt: true };
      }
      if ((state.phase !== "approving" && state.phase !== "rejecting") || state.attempt !== event.attempt) return state;
      const action: ApprovalAction = state.phase === "approving" ? "approve" : "reject";
      if (action === "approve" && isExpiry(event.error)) return { phase: "expired", at: event.at, source: "reply" };
      if (state.inDoubt && isGone(event.error)) return { phase: "checking", attempt: state.attempt, action };
      return {
        phase: "failed",
        action,
        error: event.error,
        at: event.at,
        inDoubt: state.inDoubt || event.error.retryable,
      };
    }
    case "verdict": {
      if (state.phase !== "checking" || state.attempt !== event.attempt) return state;
      const { verdict } = event;
      switch (verdict.kind) {
        case "approved":
          return {
            phase: "approved",
            outcome: verdict.outcome,
            at: verdict.at,
            by: verdict.by,
            elapsedMs: null,
            source: "audit",
          };
        case "rejected":
          return { phase: "rejected", at: verdict.at, by: verdict.by, source: "audit" };
        case "expired":
          return { phase: "expired", at: verdict.at, source: "audit" };
        case "started":
          return { phase: "unconfirmed", started: true, at: event.at };
        case "unrecorded":
          return { phase: "unconfirmed", started: false, at: event.at };
      }
    }
    case "departed":
      // only an untouched one: a request of ours (or one lost in transit) may be why it left
      if (state.phase !== "pending") return state;
      return event.overdue ? { phase: "expired", at: event.at, source: "list" } : { phase: "elsewhere", at: event.at };
  }
}

// ---------------------------------------------------------------- the audit log as witness

const IMPORT_PREFIX = "import:";

const isRejection = (problem: string | null) => problem === "rejected" || (problem?.startsWith("rejected_for:") ?? false);

/**
 * Approval-side refusals (server/routes/approvals.py) are `blocked` records under the grant id
 * with the refusal's code as `problem`. These say nothing of how the confirmation ended: someone
 * else's attempt, an approval of one that already ran (a retry after a lost reply writes it
 * after the real outcome), or of an upload not yet submitted. `approval_expired` does.
 */
const NOT_AN_OUTCOME: ReadonlySet<string> = new Set(["not_requester", "grant_used", "not_submitted"]);
const APPROVAL_EXPIRED = "approval_expired";

const isRefusal = (entry: AuditEntryOut, codes: ReadonlySet<string>) =>
  entry.status === "blocked" && entry.problem !== null && codes.has(entry.problem);

/** Records of a confirmation that are not its outcome: the request itself, and the refusals above. */
const isOutcomeRecord = (grantId: string, entry: AuditEntryOut) =>
  entry.grant_id === grantId && entry.status !== "needs_confirmation" && !isRefusal(entry, NOT_AN_OUTCOME);

function importResultOf(entry: AuditEntryOut, target: string): ImportResultOut {
  const skipped = entry.params.errors;
  return {
    entity: target.slice(IMPORT_PREFIX.length),
    written: entry.row_count ?? 0,
    skipped: typeof skipped === "number" ? skipped : 0,
    digest: entry.params_digest ?? "",
    audit_id: entry.id,
  };
}

/** The outcome an approval reply would have carried, rebuilt from its audit record (no rows, no answer). */
function outcomeOf(grantId: string, entry: AuditEntryOut): ApprovalOutcome {
  const target = entry.target ?? "";
  const kind = target.startsWith(IMPORT_PREFIX) ? "import" : "query";
  const status = entry.status === "ok" || entry.status === "blocked" || entry.status === "invalid" ? entry.status : "error";
  return {
    grant_id: grantId,
    kind,
    target,
    status,
    problem: entry.problem,
    answer: "",
    answer_source: "system",
    narrative_stripped: 0,
    columns: [],
    rows: [],
    aggregates: {},
    provenance: null,
    audit_ids: [entry.id],
    import_result: kind === "import" && status === "ok" ? importResultOf(entry, target) : null,
    trace: [],
  };
}

/**
 * What the audit log says became of `grantId`, from its records. Every write is recorded before
 * it runs (`started`) and again when it ends, and every rejection is recorded, so the newest
 * outcome record is the truth and no record means nothing ran.
 */
export function verdictFromAudit(grantId: string, entries: readonly AuditEntryOut[]): AuditVerdict {
  const latest = entries.filter((entry) => isOutcomeRecord(grantId, entry)).sort((a, b) => b.id - a.id)[0];
  if (!latest) return { kind: "unrecorded" };
  const at = Date.parse(latest.ts);
  if (latest.status === "blocked" && isRejection(latest.problem)) return { kind: "rejected", by: latest.user_id, at };
  if (latest.status === "blocked" && latest.problem === APPROVAL_EXPIRED) return { kind: "expired", at };
  if (latest.status === "started") return { kind: "started" };
  return { kind: "approved", outcome: outcomeOf(grantId, latest), by: latest.user_id, at };
}

// ---------------------------------------------------------------- expiry, for display only

/**
 * When a confirmation runs out, on this page's monotonic clock (`performance.now()`), so a
 * change of the computer's clock can neither stretch nor shrink the countdown.
 */
export interface Deadline {
  /** The `performance.now()` value at which the server's window ends. */
  endsAt: number;
  /** The confirmation's whole window, for the countdown's bar; null when not known. */
  windowMs: number | null;
}

/**
 * The moment a confirmation arrived, on the monotonic clock. The time it has left comes from the
 * server (`expires_in_s`, on its own clock), so the browser's wall clock is never read: a
 * computer whose clock is off can neither hide a confirmation that is still valid nor keep one
 * alive.
 */
export interface Arrival {
  /** `performance.now()` at receipt: to count down from. */
  mono: number;
}

export const arrivalNow = (): Arrival => ({ mono: performance.now() });

/**
 * A deadline `remainingMs` after `arrival`. With the time left the API sends on its own clock
 * (`expires_in_s`), this is the whole computation: `deadlineIn(expires_in_s * 1000, arrival)`.
 */
export function deadlineIn(remainingMs: number, arrival: Arrival, windowMs: number | null = null): Deadline {
  const left = Number.isFinite(remainingMs) ? Math.max(0, remainingMs) : 0;
  const window = windowMs !== null && windowMs > 0 ? windowMs : left > 0 ? left : null;
  return { endsAt: arrival.mono + left, windowMs: window };
}

/** The server's window for a listed confirmation: its own `expires_at - created_at`. */
export function windowOf(item: Pick<ApprovalItem, "created_at" | "expires_at">): number | null {
  const span = Date.parse(item.expires_at) - Date.parse(item.created_at);
  return Number.isFinite(span) && span > 0 ? span : null;
}

/**
 * The deadline of a listed confirmation, in the list that arrived at `arrival`: the server's
 * time left, never more than the server's own window (its bar's full length).
 */
export function listedDeadline(
  item: Pick<ApprovalItem, "created_at" | "expires_at" | "expires_in_s">,
  arrival: Arrival,
): Deadline {
  const window = windowOf(item);
  const left = item.expires_in_s * 1000;
  return deadlineIn(window !== null ? Math.min(left, window) : left, arrival, window);
}

// ---------------------------------------------------------------- the store

/** The calls the store makes; the default goes to the API. */
export interface ApprovalClient {
  approve(grantId: string): Promise<ApprovalOutcome>;
  reject(grantId: string): Promise<RejectOut>;
  /** The audit records of one confirmation. */
  records(grantId: string): Promise<AuditEntryOut[]>;
}

/** More records than one confirmation ever leaves (request, start, result, refusals). */
const RECORDS_PER_GRANT = 50;

export const apiClient: ApprovalClient = {
  approve: (grantId) => api.approve(grantId),
  reject: (grantId) => api.reject(grantId),
  records: async (grantId) => (await api.listAudit({ grant_id: grantId, page_size: RECORDS_PER_GRANT })).items,
};

export interface ApprovalStore {
  /** One confirmation's state; `PENDING` until something happens to it here. */
  get(grantId: string): ApprovalState;
  /** Every confirmation that is not `PENDING`, by grant id. A new map after each change. */
  all(): ReadonlyMap<string, ApprovalState>;
  subscribe(listener: () => void): () => void;
  /**
   * Approve, once. Returns null, and sends nothing, while a request for it is out or once it is
   * resolved (a double click, a held Enter, a second window). Otherwise resolves with the state
   * the attempt ended in, after the audit lookup when one was needed.
   */
  approve(grantId: string): Promise<ApprovalState> | null;
  /** Reject; the same rules as `approve`. */
  reject(grantId: string): Promise<ApprovalState> | null;
  /** The server stopped listing it. `overdue`: its time had run out by then. */
  depart(grantId: string, overdue: boolean): void;
}

export interface ApprovalStoreOptions {
  client?: ApprovalClient;
  /** After every attempt: the server's pending list has changed (refresh it). */
  onSettled?: (grantId: string, state: ApprovalState) => void;
  /** Wall clock, for receipts. */
  now?: () => number;
  /** Monotonic clock, for durations. */
  clock?: () => number;
}

export function createApprovalStore({
  client = apiClient,
  onSettled,
  now = () => Date.now(),
  clock = () => performance.now(),
}: ApprovalStoreOptions = {}): ApprovalStore {
  let states: ReadonlyMap<string, ApprovalState> = new Map();
  const listeners = new Set<() => void>();
  let attempts = 0;

  const get = (grantId: string) => states.get(grantId) ?? PENDING;

  const send = (grantId: string, event: ApprovalEvent): ApprovalState => {
    const before = get(grantId);
    const after = approvalReducer(before, event);
    if (after !== before) {
      const next = new Map(states);
      next.set(grantId, after);
      states = next;
      for (const listener of [...listeners]) listener();
    }
    return after;
  };

  // the reply was ambiguous: the audit log has the last word
  const check = async (grantId: string, attempt: number) => {
    try {
      const records = await client.records(grantId);
      send(grantId, { type: "verdict", attempt, verdict: verdictFromAudit(grantId, records), at: now() });
    } catch (error) {
      const failure = toFailure(error);
      send(grantId, {
        type: "failed",
        attempt,
        error: failure.retryable ? failure : { ...failure, code: AUDIT_UNREADABLE },
        at: now(),
      });
    }
  };

  const attempt = async (grantId: string, action: ApprovalAction, id: number) => {
    const started = clock();
    try {
      if (action === "approve") {
        const outcome = await client.approve(grantId);
        send(grantId, { type: "approved", attempt: id, outcome, at: now(), elapsedMs: clock() - started });
      } else {
        await client.reject(grantId);
        send(grantId, { type: "rejected", attempt: id, at: now() });
      }
    } catch (error) {
      const after = send(grantId, { type: "failed", attempt: id, error: toFailure(error), at: now() });
      if (after.phase === "checking" && after.attempt === id) await check(grantId, id);
    }
  };

  const run = (grantId: string, action: ApprovalAction): Promise<ApprovalState> | null => {
    const id = ++attempts;
    const state = send(grantId, { type: "start", action, attempt: id });
    const accepted = (state.phase === "approving" || state.phase === "rejecting") && state.attempt === id;
    if (!accepted) return null;
    return attempt(grantId, action, id).then(() => {
      const settled = get(grantId);
      onSettled?.(grantId, settled);
      return settled;
    });
  };

  return {
    get,
    all: () => states,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    approve: (grantId) => run(grantId, "approve"),
    reject: (grantId) => run(grantId, "reject"),
    depart(grantId, overdue) {
      send(grantId, { type: "departed", overdue, at: now() });
    },
  };
}
