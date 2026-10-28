import { vi } from "vitest";

import type { ApprovalsContextValue } from "@/components/providers/approvals-provider";
import { createApprovalStore, type ApprovalStoreOptions } from "@/lib/approvals";

/**
 * A stand-in for `useApprovals()` in tests that mock the provider: a real approval store (its
 * default client calls `api`, which the test mocks) whose settled attempts call `refresh`.
 */
export function approvalsValue(
  over: Partial<ApprovalsContextValue> = {},
  storeOptions: ApprovalStoreOptions = {},
): ApprovalsContextValue {
  const refresh = over.refresh ?? vi.fn();
  const items = over.items ?? [];
  return {
    items,
    count: items?.length ?? 0,
    arrival: { mono: performance.now() },
    error: null,
    store: createApprovalStore({ onSettled: refresh, ...storeOptions }),
    ...over,
    refresh,
  };
}
