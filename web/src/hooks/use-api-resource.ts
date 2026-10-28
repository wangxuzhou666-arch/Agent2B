"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isAbortError } from "@/lib/api";

export interface ApiResource<T> {
  /** The latest data that arrived; kept while a newer request loads (no flash on refetch). */
  data: T | null;
  /** True when `data` belongs to an earlier key (or there is none yet). */
  stale: boolean;
  loading: boolean;
  error: unknown;
  reload: () => void;
}

/**
 * Fetch on key change, abort the previous request, keep the last data while the next loads.
 * `key === null` pauses fetching (e.g. until a selection exists).
 */
export function useApiResource<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>): ApiResource<T> {
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<{ key: string | null; data: T | null; error: unknown }>({
    key: null,
    data: null,
    error: null,
  });
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  const fullKey = key === null ? null : `${key}#${nonce}`;
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (fullKey === null) return;
    const controller = new AbortController();
    loadRef.current(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ key: fullKey, data, error: null });
      },
      (error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => ({ key: fullKey, data: previous.data, error }));
      },
    );
    return () => controller.abort();
  }, [fullKey]);

  const settled = state.key === fullKey;
  return {
    data: state.data,
    stale: !settled || state.error !== null,
    loading: fullKey !== null && !settled,
    error: settled ? state.error : null,
    reload,
  };
}
