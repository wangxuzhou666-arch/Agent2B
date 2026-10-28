/**
 * Import wizard logic: client-side file checks, upload error codes → friendly messages, the
 * per-cell error map of a preview, and the Upload → Preview → Confirm → Done state machine.
 * The server is the authority for every check; these only fail fast and explain.
 */
import {
  ApiError,
  toFailure,
  type ApprovalRef,
  type Failure,
  type FieldOut,
  type ImportPreviewOut,
  type ImportResultOut,
  type PreviewRow,
} from "./api";
import type { ApprovalState, Deadline } from "./approvals";

export const IMPORT_STEPS = ["upload", "preview", "confirm", "done"] as const;
export type ImportStep = (typeof IMPORT_STEPS)[number];

/** Friendly-message keys (messages: `import.issue.<key>`). */
export type UploadIssueKey =
  | "tooLarge"
  | "fileType"
  | "empty"
  | "zipBomb"
  | "tooManyRows"
  | "noHeader"
  | "parseError"
  | "headerCollision"
  | "missingColumns"
  | "forbidden"
  | "digestMismatch"
  | "grantInvalid"
  | "expired"
  | "notFound"
  | "network"
  | "generic";

export interface UploadIssue {
  key: UploadIssueKey;
  /** The server's own sentence (it names columns and limits, never cell values), shown verbatim. */
  detail: string | null;
  code: string;
}

/** Stable server codes (core/guards/upload.py, core/entities/importer.py, routes). */
const ISSUE_BY_CODE: Record<string, UploadIssueKey> = {
  too_large: "tooLarge",
  payload_too_large: "tooLarge",
  file_type: "fileType",
  empty: "empty",
  zip_bomb: "zipBomb",
  too_many_rows: "tooManyRows",
  no_header: "noHeader",
  parse_error: "parseError",
  header_collision: "headerCollision",
  missing_columns: "missingColumns",
  forbidden: "forbidden",
  not_requester: "forbidden",
  digest_mismatch: "digestMismatch",
  grant_invalid: "grantInvalid",
  approval_expired: "expired",
  not_found: "notFound",
};

/** Codes whose server sentence adds something the friendly text cannot know. */
const DETAILED: ReadonlySet<UploadIssueKey> = new Set([
  "fileType",
  "tooManyRows",
  "parseError",
  "headerCollision",
  "missingColumns",
  "generic",
]);

export function uploadIssue(error: unknown): UploadIssue {
  if (!(error instanceof ApiError)) {
    return { key: "generic", detail: error instanceof Error ? error.message : null, code: "unexpected" };
  }
  return failureIssue(toFailure(error));
}

/** The same mapping for a failure already kept in state (e.g. an approval's). */
export function failureIssue(failure: Failure): UploadIssue {
  if (failure.retryable) return { key: "network", detail: null, code: failure.code }; // lost in transit
  const byStatus: UploadIssueKey | undefined =
    failure.status === 413 ? "tooLarge" : failure.status === 403 ? "forbidden" : failure.status === 410 ? "expired" : undefined;
  const key = ISSUE_BY_CODE[failure.code] ?? byStatus ?? "generic";
  return { key, detail: DETAILED.has(key) ? failure.message : null, code: failure.code };
}

/** Fail fast in the browser on what the server would reject anyway. */
export function checkFile(
  file: Pick<File, "name" | "size">,
  maxBytes: number,
  extensions: readonly string[],
): UploadIssue | null {
  const name = file.name.toLowerCase();
  if (!extensions.some((ext) => name.endsWith(ext))) return { key: "fileType", detail: null, code: "client_file_type" };
  if (file.size === 0) return { key: "empty", detail: null, code: "client_empty" };
  if (file.size > maxBytes) return { key: "tooLarge", detail: null, code: "client_too_large" };
  return null;
}

export interface CellErrors {
  /** field id → messages about that cell. */
  byField: Record<string, string[]>;
  /** Messages about the row as a whole (duplicate key, unknown fields…). */
  row: string[];
}

/**
 * Attribute each validation message of a preview row to its cell. The server writes field
 * problems as "<field label>: <problem>", so the longest label that prefixes a message names the
 * cell; anything else is about the row.
 */
export function cellErrors(row: Pick<PreviewRow, "errors">, fields: readonly Pick<FieldOut, "id" | "label">[]): CellErrors {
  const byLabel = [...fields].sort((a, b) => b.label.length - a.label.length);
  const out: CellErrors = { byField: {}, row: [] };
  for (const message of row.errors) {
    const field = byLabel.find((f) => message.startsWith(`${f.label}: `));
    if (field) (out.byField[field.id] ??= []).push(message);
    else out.row.push(message);
  }
  return out;
}

export interface UploadedFile {
  name: string;
  size: number;
}

export type WizardState =
  | { step: "upload"; entityId: string | null; busy: boolean; issue: UploadIssue | null; notice: "rejected" | "expired" | null }
  | {
      step: "preview";
      entityId: string;
      file: UploadedFile;
      preview: ImportPreviewOut;
      committing: boolean;
      issue: UploadIssue | null;
    }
  | {
      step: "confirm";
      entityId: string;
      file: UploadedFile;
      preview: ImportPreviewOut;
      /** Its state is the approval store's (see `afterApproval`). */
      approval: ApprovalRef;
      deadline: Deadline;
    }
  | { step: "done"; entityId: string; file: UploadedFile; result: ImportResultOut };

export type WizardAction =
  | { type: "selectEntity"; entityId: string }
  | { type: "uploadStart" }
  | { type: "uploadOk"; file: UploadedFile; preview: ImportPreviewOut }
  /** The same file previewed again (worded in another language): replaces the preview on screen. */
  | { type: "previewAgain"; preview: ImportPreviewOut }
  | { type: "uploadFail"; issue: UploadIssue }
  | { type: "commitStart" }
  | { type: "commitFail"; issue: UploadIssue }
  | { type: "needsConfirmation"; approval: ApprovalRef; deadline: Deadline }
  | { type: "done"; result: ImportResultOut }
  /** The confirmation can no longer be used: back to Upload with the reason. */
  | { type: "restart"; issue?: UploadIssue | null; notice?: "rejected" | "expired" | null };

export function initialWizard(entityId: string | null = null): WizardState {
  return { step: "upload", entityId, busy: false, issue: null, notice: null };
}

/** Pure transitions; an action that does not fit the current step is ignored. */
export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "selectEntity":
      return state.step === "upload" && !state.busy
        ? { ...state, entityId: action.entityId, issue: null, notice: null }
        : state;
    case "uploadStart":
      return state.step === "upload" && state.entityId ? { ...state, busy: true, issue: null, notice: null } : state;
    case "uploadOk":
      return state.step === "upload" && state.entityId
        ? { step: "preview", entityId: state.entityId, file: action.file, preview: action.preview, committing: false, issue: null }
        : state;
    case "previewAgain":
      // only over the preview of those very bytes, and not once they are being committed
      return state.step === "preview" &&
        !state.committing &&
        action.preview.digest === state.preview.digest &&
        action.preview.entity === state.preview.entity
        ? { ...state, preview: action.preview }
        : state;
    case "uploadFail":
      return state.step === "upload" ? { ...state, busy: false, issue: action.issue } : state;
    case "commitStart":
      return state.step === "preview" && state.preview.valid_count > 0 ? { ...state, committing: true, issue: null } : state;
    case "commitFail":
      return state.step === "preview" ? { ...state, committing: false, issue: action.issue } : state;
    case "needsConfirmation":
      return state.step === "preview"
        ? {
            step: "confirm",
            entityId: state.entityId,
            file: state.file,
            preview: state.preview,
            approval: action.approval,
            deadline: action.deadline,
          }
        : state;
    case "done":
      return state.step === "confirm" || state.step === "preview"
        ? { step: "done", entityId: state.entityId, file: state.file, result: action.result }
        : state;
    case "restart":
      return {
        step: "upload",
        entityId: state.entityId,
        busy: false,
        issue: action.issue ?? null,
        notice: action.notice ?? null,
      };
  }
}

/**
 * Where the Confirm step goes once its approval settles. Done with the result; back to Upload,
 * saying why, when the confirmation can no longer be used (rejected, refused, expired on approve,
 * or the run failed); null to stay: still open, lost in transit (Retry), in doubt, or resolved
 * in a way the card itself explains (elsewhere, expired while waiting).
 *
 * Expired on approve is the server's 410 to an approve, whether the reply said so (`reply`) or,
 * after a reply was lost, the audit log did (`audit`): both go back to Upload. Only `list`, which
 * ran out untouched while the user was waiting, stays.
 */
export function afterApproval(approval: ApprovalState): WizardAction | null {
  switch (approval.phase) {
    case "approved": {
      const { outcome } = approval;
      if (outcome.status === "ok" && outcome.import_result) return { type: "done", result: outcome.import_result };
      return { type: "restart", issue: { key: "generic", detail: outcome.problem ?? null, code: outcome.status } };
    }
    case "rejected":
      return { type: "restart", notice: "rejected" };
    case "expired":
      return approval.source === "list"
        ? null
        : { type: "restart", issue: { key: "expired", detail: null, code: "approval_expired" } };
    case "failed":
      return approval.error.retryable || approval.inDoubt ? null : { type: "restart", issue: failureIssue(approval.error) };
    default:
      return null;
  }
}

/** Index of a step, for the steps indicator (done steps are the ones before it). */
export function stepIndex(step: ImportStep): number {
  return IMPORT_STEPS.indexOf(step);
}
