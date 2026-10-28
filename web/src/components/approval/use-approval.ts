"use client";

import { useCallback, useSyncExternalStore } from "react";

import { useApprovals } from "@/components/providers/approvals-provider";
import { PENDING, type ApprovalState } from "@/lib/approvals";

/** One confirmation's state, shared by every window that shows it. `null`: there is none. */
export function useApprovalState(grantId: string | null): ApprovalState {
  const { store } = useApprovals();
  const read = useCallback(() => (grantId === null ? PENDING : store.get(grantId)), [store, grantId]);
  return useSyncExternalStore(store.subscribe, read, read);
}

/** Every confirmation something happened to this session, by grant id (see `ApprovalStore.all`). */
export function useApprovalStates(): ReadonlyMap<string, ApprovalState> {
  const { store } = useApprovals();
  return useSyncExternalStore(store.subscribe, store.all, store.all);
}

export interface ApprovalControls {
  state: ApprovalState;
  /** Null when ignored: a request is out, or it is resolved. */
  approve: () => Promise<ApprovalState> | null;
  reject: () => Promise<ApprovalState> | null;
}

/** State and actions of one confirmation. */
export function useApproval(grantId: string): ApprovalControls {
  const { store } = useApprovals();
  const state = useApprovalState(grantId);
  const approve = useCallback(() => store.approve(grantId), [store, grantId]);
  const reject = useCallback(() => store.reject(grantId), [store, grantId]);
  return { state, approve, reject };
}
