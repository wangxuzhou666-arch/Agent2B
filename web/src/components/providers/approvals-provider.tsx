"use client";

import { useLocale } from "next-intl";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api, type ApprovalItem } from "@/lib/api";
import { arrivalNow, createApprovalStore, listedDeadline, type ApprovalStore, type Arrival } from "@/lib/approvals";
import { APPROVALS_POLL_MS } from "@/lib/config";

export interface ApprovalsContextValue {
  /** Pending confirmations visible to the caller (their own; an admin sees everyone's). */
  items: ApprovalItem[] | null;
  count: number;
  /** When the current `items` arrived, on both clocks (countdowns start from it). */
  arrival: Arrival | null;
  /**
   * Why the last refresh failed, null once one succeeds: an `ApiError` when the server or the
   * network was at fault, any other `Error` when it was something else (it is still a failure).
   */
  error: Error | null;
  /** Re-fetch now, e.g. after a query asked for confirmation or an approval resolved. */
  refresh: () => void;
  /** What this console has done to each confirmation, for every window (see `lib/approvals`). */
  store: ApprovalStore;
}

const ApprovalsContext = createContext<ApprovalsContextValue | null>(null);

/**
 * Keeps the pending-approvals list fresh (on mount, on demand, on focus, every 30 s, and in the
 * new language when the console switches: labels and summaries are worded for the reader) and
 * holds the approval store, so Hub, Approvals and Import show one state per confirmation. A
 * confirmation that leaves the list while untouched here is marked: expired when its countdown
 * had run out (the same deadline the cards count down to), otherwise resolved elsewhere
 * (another tab, another window, an admin).
 */
export function ApprovalsProvider({ children, store: given }: { children: ReactNode; store?: ApprovalStore }) {
  const locale = useLocale();
  const [items, setItems] = useState<ApprovalItem[] | null>(null);
  const [arrival, setArrival] = useState<Arrival | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  const listed = useRef<{ items: ApprovalItem[]; arrival: Arrival } | null>(null);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  const [store] = useState(() => given ?? createApprovalStore({ onSettled: refresh }));

  useEffect(() => {
    const controller = new AbortController();
    api
      .listApprovals(controller.signal)
      .then((list) => {
        const before = listed.current;
        const arrived = arrivalNow();
        listed.current = { items: list.items, arrival: arrived };
        setItems(list.items);
        setArrival(arrived);
        setError(null);
        if (!before) return;
        const still = new Set(list.items.map((item) => item.grant_id));
        for (const item of before.items) {
          if (still.has(item.grant_id)) continue;
          store.depart(item.grant_id, listedDeadline(item, before.arrival).endsAt <= arrived.mono);
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      });
    return () => controller.abort();
    // `locale`: the list is fetched again in the language just switched to (the request carries it)
  }, [tick, store, locale]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, APPROVALS_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  const value = useMemo<ApprovalsContextValue>(
    () => ({ items, count: items?.length ?? 0, arrival, error, refresh, store }),
    [items, arrival, error, refresh, store],
  );
  return <ApprovalsContext.Provider value={value}>{children}</ApprovalsContext.Provider>;
}

export function useApprovals(): ApprovalsContextValue {
  const value = useContext(ApprovalsContext);
  if (!value) throw new Error("useApprovals must be used inside <ApprovalsProvider>");
  return value;
}
