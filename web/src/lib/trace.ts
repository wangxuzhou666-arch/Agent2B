/**
 * Turns backend TraceStep frames into display lines. Every line is built from facts the server
 * sent (step, status, source, facts, duration_ms); nothing here invents reasoning. Labels are
 * message keys under `trace.step`, so both locales stay in messages/*.json. A failed model call
 * is shown by its call, error class and HTTP status only (`modelFailure` reads the same record
 * for the line under an answer), never by its message.
 */
import type messages from "../../messages/en.json";
import type { QueryOut, RouteSource, TraceStep } from "./api";

/** A model call that failed while answering, as an answer carries it (`model_errors`). */
export type ModelCallError = NonNullable<QueryOut["model_errors"]>[number];

export type TraceStepKey = keyof (typeof messages)["trace"]["step"];
export type TraceActiveKey = keyof (typeof messages)["trace"]["active"];

/** Dot colour: ok = green, empty = zero rows (a result, not an error), warn, error, neutral. */
export type TraceTone = "ok" | "empty" | "warn" | "error" | "neutral";

export interface TraceLine {
  id: string;
  key: TraceStepKey;
  values: Record<string, string | number>;
  tone: TraceTone;
  source: RouteSource | null;
  durationMs: number | null;
  /** Verbatim server text that explains the line (e.g. the router's reason). */
  note?: string;
}

type Facts = Record<string, unknown>;

const str = (facts: Facts, key: string): string | undefined => {
  const value = facts[key];
  return typeof value === "string" && value !== "" ? value : undefined;
};
const num = (facts: Facts, key: string): number | undefined => {
  const value = facts[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
};
const list = (facts: Facts, key: string): string[] => {
  const value = facts[key];
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
};

const toneOf = (status: TraceStep["status"]): TraceTone =>
  status === "ok" ? "ok" : status === "error" ? "error" : status === "blocked" ? "warn" : "neutral";

const UNKNOWN = "?";

/** A call or an error class: a name, never a message (a message may echo the request). */
const IDENTIFIER = /^[A-Za-z_][\w.]{0,63}$/;
/** The `status` of a failure without an HTTP status (a timeout, an unparseable reply). */
const NO_STATUS = "none";

const identifier = (value: unknown): string =>
  typeof value === "string" && IDENTIFIER.test(value) ? value : UNKNOWN;

/** A failed model call as a line names it. `status` is an HTTP status code or `NO_STATUS`. */
interface CallFailure {
  call: string;
  error: string;
  status: string;
}

/**
 * A `model_error` fact (`{call, error, http_status}`), or null when the value is not one. Only
 * names are shown: an `error` that is not a class name reads "?", whatever it holds.
 */
function callFailure(value: unknown): CallFailure | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const fact = value as Facts;
  const status = fact.http_status;
  const valid = typeof status === "number" && Number.isInteger(status) && status >= 100 && status <= 599;
  return { call: identifier(fact.call), error: identifier(fact.error), status: valid ? String(status) : NO_STATUS };
}

const callFailures = (value: unknown): CallFailure[] =>
  Array.isArray(value) ? value.map(callFailure).filter((f): f is CallFailure => f !== null) : [];

function understand(step: TraceStep, facts: Facts): Omit<TraceLine, "id" | "durationMs"> {
  const kind = str(facts, "kind");
  const target = str(facts, "target") ?? UNKNOWN;
  const options = list(facts, "options");
  const base = { tone: toneOf(step.status), source: step.source ?? null, note: str(facts, "reason") };
  if (kind === "chat") return { ...base, key: "routeChat", values: {} };
  if (kind === "open_task") return { ...base, key: "routeAgent", values: {} };
  if (kind === "clarify") {
    const first = options[0];
    if (options.length === 1 && first) {
      const key = step.source === "llm_confirm" ? "routeLlmConfirm" : "routeConfirm";
      return { ...base, tone: "warn", key, values: { target: first } };
    }
    if (options.length > 1) {
      return { ...base, tone: "warn", key: "routeClarify", values: { count: options.length } };
    }
    return { ...base, tone: "warn", key: "gateBlocked", values: {} };
  }
  switch (step.source) {
    case "rule":
      return { ...base, key: "routeRule", values: { target } };
    case "explicit":
      return { ...base, key: "routeExplicit", values: { target } };
    case "llm":
      return { ...base, key: "routeLlm", values: { target } };
    case "llm_confirm":
      return { ...base, key: "routeLlmConfirm", values: { target } };
    case "semantic":
      return { ...base, key: "routeSemantic", values: { target } };
    case "agent":
      return { ...base, key: "routeAgent", values: {} };
    default:
      return { ...base, key: "routeOther", values: { target } };
  }
}

const nested = (facts: Facts, key: string): Facts | undefined => {
  const value = facts[key];
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Facts) : undefined;
};

/**
 * The router's hand-over to exploration (reason `intent_not_covered`: no fixed flow covers the
 * question), with the closest semantic match and the threshold it was measured against when the
 * evidence names one. Another reason reads verbatim.
 */
function handover(step: TraceStep, facts: Facts): Omit<TraceLine, "id" | "durationMs"> {
  const base = { tone: toneOf(step.status), source: step.source ?? null };
  const reason = str(facts, "reason") ?? UNKNOWN;
  if (reason !== "intent_not_covered") return { ...base, key: "handoverOther", values: { reason } };
  const semantic = nested(nested(facts, "evidence") ?? {}, "semantic") ?? {};
  const target = str(semantic, "best");
  const score = num(semantic, "score");
  const threshold = num(semantic, "threshold");
  if (target && score !== undefined && threshold !== undefined) {
    return { ...base, key: "handoverClosest", values: { target, score, threshold } };
  }
  return { ...base, key: "handover", values: {} };
}

function gate(step: TraceStep, facts: Facts): Omit<TraceLine, "id" | "durationMs"> {
  const source = step.source ?? null;
  if (step.status === "ok") {
    const used = str(facts, "confirmation") === "consumed";
    return { key: used ? "gateApprovalUsed" : "gateOk", values: {}, tone: "ok", source };
  }
  if (step.status === "skipped") {
    return { key: "gateWaitingChoice", values: {}, tone: "neutral", source };
  }
  if (step.status === "error") return { key: "gateError", values: {}, tone: "error", source };
  const stage = str(facts, "stage");
  const warn = { tone: "warn" as const, source };
  switch (stage) {
    case "confirmation":
      return str(facts, "grant_id")
        ? { ...warn, key: "gateNeedsApproval", values: {} }
        : { ...warn, key: "gateApprovalInvalid", values: {} };
    case "authz":
      return { ...warn, key: "gateAuthz", values: {} };
    case "precondition":
      return { ...warn, key: "gatePrecondition", values: { unmet: list(facts, "unmet").join(", ") || UNKNOWN } };
    case "params":
      return { ...warn, key: "gateParams", values: {} };
    case "lookup":
      return { ...warn, key: "gateLookup", values: {} };
    case undefined:
      return { ...warn, key: "gateBlocked", values: {}, note: str(facts, "reason") };
    default:
      return { ...warn, key: "gateBlockedStage", values: { stage } };
  }
}

function execute(step: TraceStep, facts: Facts): Omit<TraceLine, "id" | "durationMs"> {
  const source = step.source ?? null;
  const tone = toneOf(step.status);
  const workflow = str(facts, "workflow");
  if (workflow) {
    return {
      key: "executeWorkflowStep",
      values: { workflow, step: str(facts, "step") ?? UNKNOWN, kind: str(facts, "kind") ?? UNKNOWN },
      tone,
      source,
    };
  }
  const loop = str(facts, "loop");
  if (loop === "start") {
    // what the explorer was given: its runtime and every tool it may call
    const exposed = list(facts, "exposed");
    return {
      key: "executeAgentStart",
      values: { runtime: str(facts, "runtime") ?? UNKNOWN, count: exposed.length, tools: exposed.join(", ") || UNKNOWN },
      tone,
      source,
    };
  }
  if (loop === "refused") {
    // the explore node's guard said no: nothing ran (`outcome`: the earlier one, when that was why)
    return {
      key: "executeAgentRefused",
      values: { refused: str(facts, "refused") ?? UNKNOWN, outcome: str(facts, "outcome") ?? UNKNOWN },
      tone,
      source,
    };
  }
  if (loop) {
    const turn = num(facts, "turn") ?? 0;
    if (step.status === "error") {
      return { key: "executeAgentError", values: { turn, error: str(facts, "error") ?? UNKNOWN }, tone, source };
    }
    if (loop === "stopped") {
      const reason = (str(facts, "reason") ?? UNKNOWN).replaceAll("_", " ");
      return { key: "executeAgentStopped", values: { reason }, tone, source };
    }
    if (loop === "answer") return { key: "executeAgentAnswer", values: { turn }, tone, source };
    return {
      key: "executeAgentTurn",
      values: { turn, tools: list(facts, "requested").join(", ") || UNKNOWN },
      tone,
      source,
    };
  }
  if (step.status === "ok") {
    const count = num(facts, "row_count") ?? 0;
    return { key: "executeRows", values: { count }, tone: count === 0 ? "empty" : "ok", source };
  }
  const error = str(facts, "error") ?? UNKNOWN;
  if (step.status === "blocked") {
    if (facts.needs_input === true) return { key: "executeNeedsInput", values: {}, tone, source };
    return { key: "executeBlocked", values: { error }, tone, source };
  }
  const rolledBack = facts.rolled_back === true;
  return { key: rolledBack ? "executeRolledBack" : "executeError", values: { error }, tone, source };
}

/**
 * How a list of named items reads in the current language. The defaults are English; the trace
 * view passes its locale's words (`trace.notItem`, `trace.itemSeparator`).
 */
export interface TraceWords {
  /** An excluded item ("except packaging"): "not packaging". */
  not: (item: string) => string;
  join: (items: readonly string[]) => string;
}

const ENGLISH: TraceWords = { not: (item) => `not ${item}`, join: (items) => items.join(", ") };

type Mention = Record<string, unknown>;

const isMention = (item: unknown): item is Mention => typeof item === "object" && item !== null;
const scalar = (value: unknown): string | null =>
  typeof value === "string" || typeof value === "number" ? String(value) : null;
/** The scope guard identifies a constraint by `key=value` (its acknowledgement id). */
const mentionId = (m: Mention) => `${scalar(m.key) ?? ""}=${scalar(m.value) ?? ""}`;

/**
 * The words the question used for each constraint. Only `mentioned` carries them (`text`);
 * `bound` / `unbound` / `conflicts` name the same constraints by key and value alone.
 */
function mentionTexts(facts: Facts): Map<string, string> {
  const texts = new Map<string, string>();
  const mentioned = facts.mentioned;
  if (!Array.isArray(mentioned)) return texts;
  for (const m of mentioned) {
    if (!isMention(m)) continue;
    const text = typeof m.text === "string" ? m.text.trim() : "";
    if (text && !texts.has(mentionId(m))) texts.set(mentionId(m), text);
  }
  return texts;
}

/**
 * A list-or-count fact: `["SKU-10042"]` → 1 item named; `2` → 2 items, unnamed. A scope
 * mention object is named by its display `label` when the server sends one, else by the words
 * the question used (`text`, or the matching `mentioned` entry's), else by its `value` / `name`
 * / `id`; an excluded (negated) one reads "not …".
 */
function mentions(
  facts: Facts,
  key: string,
  texts: Map<string, string>,
  words: TraceWords,
): { count: number; names: string[] } {
  const value = facts[key];
  if (typeof value === "number" && Number.isFinite(value)) return { count: value, names: [] };
  if (!Array.isArray(value)) return { count: 0, names: [] };
  const names = value
    .map((item) => {
      const plain = scalar(item);
      if (plain !== null) return plain;
      if (!isMention(item)) return null;
      const raw = scalar(item.value) ?? scalar(item.name) ?? scalar(item.id);
      if (raw === null) return null;
      const label = typeof item.label === "string" && item.label.trim() ? item.label.trim() : null;
      const text = typeof item.text === "string" && item.text.trim() ? item.text.trim() : null;
      const name = label ?? text ?? texts.get(mentionId(item)) ?? raw;
      return item.negated === true ? words.not(name) : name;
    })
    .filter((name): name is string => name !== null && name !== "");
  return { count: value.length, names };
}

/**
 * Scope guard: did every constraint the question named (a warehouse, a category…) end up in
 * the parameters? Verdicts: pass, gap (named but not covered), conflict (contradicts a
 * parameter), unfiltered (the user chose to run without it).
 */
function scope(step: TraceStep, facts: Facts, words: TraceWords): Omit<TraceLine, "id" | "durationMs"> {
  const source = step.source ?? null;
  if (step.status === "error") return { key: "scopeError", values: {}, tone: "error", source, note: str(facts, "error") };
  const verdict = str(facts, "verdict");
  const texts = mentionTexts(facts);
  const mentioned = mentions(facts, "mentioned", texts, words);
  const bound = mentions(facts, "bound", texts, words);
  const unbound = mentions(facts, "unbound", texts, words);
  const conflicts = mentions(facts, "conflicts", texts, words);
  const items = (m: { names: string[] }) => (m.names.length > 0 ? words.join(m.names) : UNKNOWN);
  if (verdict === "conflict" || conflicts.count > 0) {
    return { key: "scopeConflict", values: { count: conflicts.count, items: items(conflicts) }, tone: "warn", source };
  }
  if (verdict === "unfiltered" || (facts.acknowledged === true && unbound.count > 0)) {
    return { key: "scopeUnfiltered", values: { count: unbound.count, items: items(unbound) }, tone: "neutral", source };
  }
  if (unbound.count > 0) {
    return { key: "scopeUnbound", values: { count: unbound.count, items: items(unbound) }, tone: "warn", source };
  }
  if (mentioned.count === 0 && bound.count === 0) return { key: "scopeNone", values: {}, tone: toneOf(step.status), source };
  return { key: "scopeOk", values: { count: Math.max(bound.count, mentioned.count) }, tone: toneOf(step.status), source };
}

/**
 * Who wrote the answer, in three states a reader can tell apart:
 * - no model configured: the zero-key mock answers on the model's path with template text, so
 *   it says so whatever `source` names (the path the text took, not who wrote it);
 * - a configured model whose call failed (`model_error`): template text stands in for it;
 * - the model.
 * Template text with no failure behind it (the lock stripped every sentence) says just that.
 */
function narration(
  facts: Facts,
  by: string | undefined,
  source: RouteSource | null,
): Omit<TraceLine, "id" | "durationMs"> {
  // the mock first: no model was configured to fail (the respond step still lists any failure)
  if (facts.mock === true) return { key: "narrateMock", values: {}, tone: "ok", source };
  // the server records a failure only when template text stands in; model text is the model's
  const failure = by === "model" ? null : callFailure(facts.model_error);
  if (failure) {
    return { key: "narrateFailed", values: { error: failure.error, status: failure.status }, tone: "warn", source };
  }
  return { key: by === "model" ? "narrateModel" : "narrateTemplate", values: {}, tone: "ok", source };
}

/** Facts of a step this build does not know, as a short `key=value` line (primitives only). */
function factsNote(facts: Facts): string | undefined {
  const parts = Object.entries(facts)
    .filter(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v))
    .slice(0, 4)
    .map(([k, v]) => `${k}=${v === null ? "null" : String(v)}`);
  const text = parts.join(", ");
  return text ? (text.length > 120 ? `${text.slice(0, 119)}…` : text) : undefined;
}

/**
 * One backend step → its display lines: one, plus the numeric lock for narrate, and for respond
 * each failed model call and any egress redaction.
 */
export function describeStep(step: TraceStep, index: number, words: TraceWords = ENGLISH): TraceLine[] {
  const facts: Facts = step.facts ?? {};
  const durationMs = step.duration_ms && step.duration_ms > 0 ? step.duration_ms : null;
  const line = (part: Omit<TraceLine, "id" | "durationMs">, suffix = "", ms = durationMs): TraceLine => ({
    ...part,
    id: `${index}${suffix}`,
    durationMs: ms,
  });
  const source = step.source ?? null;

  switch (step.step) {
    case "candidates":
      if (step.status === "error") {
        return [
          line({
            key: "candidatesError",
            values: { name: str(facts, "precondition") ?? UNKNOWN },
            tone: "error",
            source,
            note: str(facts, "error"),
          }),
        ];
      }
      return [line({ key: "candidates", values: { count: num(facts, "count") ?? 0 }, tone: "ok", source })];
    case "understand":
      return [line(understand(step, facts))];
    case "handover":
      return [line(handover(step, facts))];
    case "scope":
      return [line(scope(step, facts, words))];
    case "gate":
      return [line(gate(step, facts))];
    case "execute":
      return [line(execute(step, facts))];
    case "narrate": {
      const by = str(facts, "source");
      const stripped = num(facts, "stripped") ?? 0;
      const lines = [line(narration(facts, by, source))];
      // The lock only runs over model-written text; a template cannot cite unverified numbers.
      if (by === "model" || stripped > 0) {
        lines.push(
          line(
            stripped > 0
              ? { key: "numericLockStripped", values: { count: stripped }, tone: "warn", source }
              : { key: "numericLockClean", values: {}, tone: "ok", source },
            "-lock",
            null,
          ),
        );
      }
      return lines;
    }
    case "respond": {
      // every model call of the request that failed, routing included: one line each
      const failed = callFailures(facts.model_errors).map(({ call, error, status }, i) =>
        line({ key: "modelCallFailed", values: { call, error, status }, tone: "warn", source }, `-model-${i}`, null),
      );
      if (step.status === "error") {
        return [line({ key: "respondError", values: {}, tone: "error", source }), ...failed];
      }
      const lines = [
        line({ key: "respondOk", values: { count: num(facts, "rows") ?? 0 }, tone: "ok", source }),
        ...failed,
      ];
      const redacted = num(facts, "redacted") ?? 0;
      if (redacted > 0) {
        lines.push(
          line({ key: "egressRedacted", values: { count: redacted }, tone: "neutral", source }, "-egress", null),
        );
      }
      return lines;
    }
    default:
      // a step this build has never seen: its name and status verbatim, plus its plain facts
      return [
        line({
          key: "unknown",
          values: { step: step.step, status: step.status },
          tone: toneOf(step.status),
          source,
          note: factsNote(facts),
        }),
      ];
  }
}

export function describeTrace(steps: readonly TraceStep[], words: TraceWords = ENGLISH): TraceLine[] {
  return steps.flatMap((step, index) => describeStep(step, index, words));
}

/** The route-source of the turn, from its `understand` step (what the audit records). */
export function routeSourceOf(steps: readonly TraceStep[]): RouteSource | null {
  for (const step of steps) if (step.step === "understand") return step.source ?? null;
  return null;
}

/** The calls that write text; when one fails, template text stands in for it. */
const TEXT_CALLS: ReadonlySet<string> = new Set(["narrate", "chat", "agent_step"]);

/** What stood in for the failed calls: template text, or (routing) rules and keyword search. */
export type ModelFallback = "template" | "rules" | "none";

/**
 * The line under an answer whose model calls failed (`model_errors`): each failed call once, in
 * the order it first failed, and what stood in for it. A failed text call left template text
 * (`answer_source` "template"); a failed routing call left routing to the rules and keyword
 * search. Null when no call failed: neither the zero-key mock nor a model that answered sends any.
 */
export function modelFailure(
  errors: readonly ModelCallError[] | null | undefined,
  answerSource: QueryOut["answer_source"],
): { calls: string[]; fallback: ModelFallback } | null {
  const calls = [...new Set(callFailures(errors).map((f) => f.call))];
  if (calls.length === 0) return null;
  const textFailed = calls.some((call) => TEXT_CALLS.has(call));
  const fallback: ModelFallback =
    textFailed && answerSource === "template" ? "template" : calls.includes("route") ? "rules" : "none";
  return { calls, fallback };
}

/** How an exploration run that did not finish ended, from its trace. */
export interface ExploreStop {
  /** The run's `stop_reason`: a spent budget (`max_turns`, `max_tool_calls`) or a failure. */
  reason: string;
  /** Why the worker was unavailable (`worker_unavailable`'s `detail`), else `NO_DETAIL`. */
  detail: string;
  /** warn: a budget stopped the run on purpose; error: anything else. */
  tone: "warn" | "error";
}

/** Runs that ended as they should: with an answer, or with a write held for approval. */
const FINISHED: ReadonlySet<string> = new Set(["answered", "needs_confirmation"]);
const BUDGETS: ReadonlySet<string> = new Set(["max_turns", "max_tool_calls"]);
/** The `detail` of a stop that has none. */
const NO_DETAIL = "none";

/**
 * The stop of an exploration run that did not finish, read from the one `execute` step that
 * carries `stop_reason` (the step that ends the run). Null for a turn that did not explore, and
 * for a run that answered or waits for an approval. A reason or detail that is not an
 * identifier reads "?".
 */
export function exploreStop(steps: readonly TraceStep[]): ExploreStop | null {
  const end = steps.find((s) => s.step === "execute" && typeof (s.facts ?? {}).stop_reason === "string");
  if (!end) return null;
  const facts: Facts = end.facts ?? {};
  const reason = identifier(facts.stop_reason);
  if (FINISHED.has(reason)) return null;
  return {
    reason,
    detail: typeof facts.detail === "string" ? identifier(facts.detail) : NO_DETAIL,
    tone: BUDGETS.has(reason) ? "warn" : "error",
  };
}

/** What the engine is doing next, from the last step received (graph order, not guesswork). */
export function activeLabel(steps: readonly TraceStep[]): {
  key: TraceActiveKey;
  values: Record<string, string>;
} {
  const last = steps.at(-1);
  if (!last || last.step === "candidates") return { key: "understanding", values: {} };
  const facts: Facts = last.facts ?? {};
  if (last.step === "understand") {
    const kind = str(facts, "kind");
    if (kind === "clarify") return { key: "options", values: {} };
    if (kind === "chat") return { key: "writing", values: {} };
    if (kind === "open_task") return { key: "planning", values: {} };
    return { key: "running", values: { target: str(facts, "target") ?? UNKNOWN } };
  }
  if (last.step === "scope") {
    // passed: the gate and the run still follow; blocked: the choices are being prepared
    if (last.status === "ok") return { key: "running", values: { target: str(facts, "target") ?? UNKNOWN } };
    if (last.status === "blocked") return { key: "options", values: {} };
    return { key: "finishing", values: {} };
  }
  if (last.step === "handover") return { key: "planning", values: {} };
  if (last.step === "gate") {
    if (last.status === "ok") return { key: "running", values: { target: str(facts, "target") ?? UNKNOWN } };
    return { key: "finishing", values: {} };
  }
  if (last.step === "execute") {
    if (str(facts, "loop")) {
      // the step that ends a run carries its stop reason: the answer is written next, unless a
      // write waits for approval
      const stop = str(facts, "stop_reason");
      if (stop === undefined) return { key: "planning", values: {} };
      return stop === "needs_confirmation" ? { key: "finishing", values: {} } : { key: "writing", values: {} };
    }
    return last.status === "ok" ? { key: "writing", values: {} } : { key: "finishing", values: {} };
  }
  return { key: "finishing", values: {} };
}
