import { useSyncExternalStore } from "react";

const subscribe = () => () => undefined;

/** False during SSR and hydration, true afterwards (for values only the browser knows). */
export function useMounted(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
