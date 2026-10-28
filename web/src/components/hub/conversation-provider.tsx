"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";

import { useApprovals } from "@/components/providers/approvals-provider";
import { isAbortError, streamQuery, toFailure, type ExplicitParams } from "@/lib/api";
import { arrivalNow, deadlineIn } from "@/lib/approvals";

import {
  conversationReducer,
  initialConversation,
  type Turn,
  type TurnError,
} from "./conversation-state";

interface ConversationContextValue {
  turns: Turn[];
  /** A query is streaming; sending is locked until it ends or is stopped. */
  busy: boolean;
  /**
   * Ask a question. A picked clarify option re-sends it with `explicitTarget` and, when the
   * option carries them, `explicitParams` (passed to the server untouched).
   */
  send: (query: string, options?: SendOptions) => void;
  stop: () => void;
  retry: (turnId: string) => void;
  reset: () => void;
}

export interface SendOptions {
  explicitTarget?: string;
  explicitParams?: ExplicitParams | null;
}

const ConversationContext = createContext<ConversationContextValue | null>(null);

/**
 * A question that failed. It may be asked again only when it was lost in transit (network,
 * timeout, a gateway that could not reach the API, an answer stream cut short or unreadable). A
 * server `error` frame is a verdict, like any other answer: re-sending a write would mint a
 * second grant.
 */
export function toQueryError(error: unknown): TurnError {
  return toFailure(error);
}

let turnSeq = 0;
const nextTurnId = () => `turn-${Date.now().toString(36)}-${(turnSeq++).toString(36)}`;

/**
 * Holds the Hub conversation for the whole console session, so leaving Hub for another window
 * and coming back keeps every turn. Memory only: nothing about answers or rows is persisted.
 */
export function ConversationProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(conversationReducer, initialConversation);
  const { refresh: refreshApprovals } = useApprovals();
  const controllerRef = useRef<AbortController | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const busy = state.turns.some((turn) => turn.status === "streaming");

  useEffect(() => () => controllerRef.current?.abort(), []);

  const run = useCallback(
    async (
      id: string,
      query: string,
      explicitTarget: string | null,
      explicitParams: ExplicitParams | null,
      started: number,
    ) => {
      const controller = new AbortController();
      controllerRef.current = controller;
      try {
        const response = await streamQuery(
          {
            query,
            explicit_target: explicitTarget,
            explicit_params: explicitParams,
            conversation_id: conversationIdRef.current,
          },
          {
            signal: controller.signal,
            onTrace: (step) => dispatch({ type: "trace", id, step }),
          },
        );
        conversationIdRef.current = response.conversation_id;
        const arrival = arrivalNow();
        dispatch({
          type: "respond",
          id,
          response,
          elapsedMs: arrival.mono - started,
          // fixed now, at receipt: the time left on the server's clock, counted down on this page's
          approvalDeadline: response.approval ? deadlineIn(response.approval.expires_in_s * 1000, arrival) : null,
        });
        if (response.approval) refreshApprovals();
      } catch (error) {
        const elapsedMs = performance.now() - started;
        if (isAbortError(error)) dispatch({ type: "cancel", id, elapsedMs });
        else dispatch({ type: "fail", id, error: toQueryError(error), elapsedMs });
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
      }
    },
    [refreshApprovals],
  );

  const send = useCallback(
    (query: string, options?: SendOptions) => {
      const text = query.trim();
      if (!text || controllerRef.current) return;
      const id = nextTurnId();
      const explicitTarget = options?.explicitTarget ?? null;
      // params only ever go with the target they were offered for
      const explicitParams = explicitTarget ? (options?.explicitParams ?? null) : null;
      const startedAt = performance.now();
      dispatch({ type: "start", id, query: text, explicitTarget, explicitParams, startedAt });
      void run(id, text, explicitTarget, explicitParams, startedAt);
    },
    [run],
  );

  const stop = useCallback(() => controllerRef.current?.abort(), []);

  const turnsRef = useRef(state.turns);
  useEffect(() => {
    turnsRef.current = state.turns;
  }, [state.turns]);

  const retry = useCallback(
    (turnId: string) => {
      const turn = turnsRef.current.find((t) => t.id === turnId);
      // only a question lost in transit is asked again; a verdict stands
      if (!turn || controllerRef.current || turn.status !== "error" || !turn.error?.retryable) return;
      const startedAt = performance.now();
      dispatch({ type: "restart", id: turnId, startedAt });
      void run(turnId, turn.query, turn.explicitTarget, turn.explicitParams, startedAt);
    },
    [run],
  );

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    conversationIdRef.current = null;
    dispatch({ type: "reset" });
  }, []);

  const value = useMemo<ConversationContextValue>(
    () => ({ turns: state.turns, busy, send, stop, retry, reset }),
    [state.turns, busy, send, stop, retry, reset],
  );
  return <ConversationContext.Provider value={value}>{children}</ConversationContext.Provider>;
}

export function useConversation(): ConversationContextValue {
  const value = useContext(ConversationContext);
  if (!value) throw new Error("useConversation must be used inside <ConversationProvider>");
  return value;
}
