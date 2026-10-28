/** Hub conversation state: a list of turns driven by a pure reducer (unit-tested). */
import type { ExplicitParams, Failure, QueryOut, TraceStep } from "@/lib/api";
import type { Deadline } from "@/lib/approvals";

export type TurnStatus = "streaming" | "done" | "error" | "cancelled";

/** Why a question failed; `retryable` only when it was lost in transit (see `toFailure`). */
export type TurnError = Failure;

export interface Turn {
  id: string;
  query: string;
  /** Set when the user picked a clarify option: the run skips routing (source `explicit`). */
  explicitTarget: string | null;
  /**
   * The picked option's `explicit_params`, kept verbatim so a retry sends the same request (a
   * scope choice's one-shot `_scope_ack` rides here). Never carried over to the next question.
   */
  explicitParams: ExplicitParams | null;
  status: TurnStatus;
  /** `performance.now()` when the current attempt started (retry restarts it). */
  startedAt: number;
  steps: TraceStep[];
  response: QueryOut | null;
  error: TurnError | null;
  /** Wall-clock time from send to the last frame, measured by the client. */
  elapsedMs: number | null;
  /**
   * When the write this answer holds for confirmation (`response.approval`) runs out, fixed when
   * the answer arrived. Its state is the approval store's, shared with every other window.
   */
  approvalDeadline: Deadline | null;
}

export type ConversationAction =
  | {
      type: "start";
      id: string;
      query: string;
      explicitTarget: string | null;
      explicitParams: ExplicitParams | null;
      startedAt: number;
    }
  | { type: "trace"; id: string; step: TraceStep }
  | { type: "respond"; id: string; response: QueryOut; elapsedMs: number; approvalDeadline: Deadline | null }
  | { type: "fail"; id: string; error: TurnError; elapsedMs: number }
  | { type: "cancel"; id: string; elapsedMs: number }
  | { type: "restart"; id: string; startedAt: number }
  | { type: "reset" };

export interface ConversationState {
  turns: Turn[];
}

export const initialConversation: ConversationState = { turns: [] };

function update(state: ConversationState, id: string, change: (turn: Turn) => Turn): ConversationState {
  let changed = false;
  const turns = state.turns.map((turn) => {
    if (turn.id !== id) return turn;
    const next = change(turn);
    if (next !== turn) changed = true;
    return next;
  });
  return changed ? { turns } : state;
}

export function conversationReducer(
  state: ConversationState,
  action: ConversationAction,
): ConversationState {
  switch (action.type) {
    case "start":
      return {
        turns: [
          ...state.turns,
          {
            id: action.id,
            query: action.query,
            explicitTarget: action.explicitTarget,
            explicitParams: action.explicitParams,
            status: "streaming",
            startedAt: action.startedAt,
            steps: [],
            response: null,
            error: null,
            elapsedMs: null,
            approvalDeadline: null,
          },
        ],
      };
    case "trace":
      return update(state, action.id, (turn) =>
        turn.status === "streaming" ? { ...turn, steps: [...turn.steps, action.step] } : turn,
      );
    case "respond":
      return update(state, action.id, (turn) =>
        turn.status === "streaming"
          ? {
              ...turn,
              status: "done",
              response: action.response,
              // the response carries the full trace; prefer it over what streamed in
              steps: action.response.trace.length > 0 ? action.response.trace : turn.steps,
              elapsedMs: action.elapsedMs,
              approvalDeadline: action.response.approval ? action.approvalDeadline : null,
            }
          : turn,
      );
    case "fail":
      return update(state, action.id, (turn) =>
        turn.status === "streaming"
          ? { ...turn, status: "error", error: action.error, elapsedMs: action.elapsedMs }
          : turn,
      );
    case "cancel":
      return update(state, action.id, (turn) =>
        turn.status === "streaming" ? { ...turn, status: "cancelled", elapsedMs: action.elapsedMs } : turn,
      );
    case "restart":
      return update(state, action.id, (turn) => ({
        ...turn,
        status: "streaming",
        startedAt: action.startedAt,
        steps: [],
        response: null,
        error: null,
        elapsedMs: null,
        approvalDeadline: null,
      }));
    case "reset":
      return initialConversation;
  }
}

/** The answer failed on the server side even though the transport succeeded. */
export function responseError(turn: Turn): string | null {
  return turn.response?.error ?? null;
}
