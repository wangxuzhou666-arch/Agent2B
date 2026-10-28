import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { useApiResource } from "./use-api-resource";

const unavailable = new ApiError({ status: 500, code: "internal_error", message: "boom", kind: "http" });

/** A loader whose answers the test hands out one by one, per call. */
function scripted<T>() {
  const calls: { key: string; signal: AbortSignal; resolve: (value: T) => void; reject: (error: unknown) => void }[] = [];
  const load = (key: string) =>
    vi.fn(
      (signal: AbortSignal) =>
        new Promise<T>((resolve, reject) => {
          calls.push({ key, signal, resolve, reject });
        }),
    );
  return { calls, load };
}

describe("useApiResource", () => {
  it("flags the previous key's data as stale after the next key fails, instead of passing it off as current", async () => {
    const { calls, load } = scripted<string[]>();
    const { result, rerender } = renderHook(({ key }) => useApiResource(key, load(key)), { initialProps: { key: "page-1" } });
    expect(result.current).toMatchObject({ data: null, stale: true, loading: true, error: null });

    await act(async () => calls[0]!.resolve(["row from page 1"]));
    expect(result.current).toMatchObject({ data: ["row from page 1"], stale: false, loading: false, error: null });

    rerender({ key: "page-2" });
    // while page 2 loads, page 1's rows stay on screen (no flash), marked as not page 2's
    expect(result.current).toMatchObject({ data: ["row from page 1"], stale: true, loading: true, error: null });

    await act(async () => calls[1]!.reject(unavailable));
    expect(result.current).toMatchObject({ data: ["row from page 1"], stale: true, loading: false, error: unavailable });
  });

  it("keeps the data stale after a failed reload of the same key, until a reload succeeds", async () => {
    const { calls, load } = scripted<string>();
    const { result } = renderHook(() => useApiResource("list", load("list")));
    await act(async () => calls[0]!.resolve("v1"));
    expect(result.current).toMatchObject({ data: "v1", stale: false });

    act(() => result.current.reload());
    await act(async () => calls[1]!.reject(unavailable));
    expect(result.current).toMatchObject({ data: "v1", stale: true, loading: false, error: unavailable });

    act(() => result.current.reload());
    await act(async () => calls[2]!.resolve("v2"));
    expect(result.current).toMatchObject({ data: "v2", stale: false, loading: false, error: null });
  });

  it("aborts the request of a key it left, and ignores whatever that request does next", async () => {
    const { calls, load } = scripted<string>();
    const { result, rerender } = renderHook(({ key }) => useApiResource(key, load(key)), { initialProps: { key: "a" } });
    rerender({ key: "b" });
    expect(calls[0]!.signal.aborted).toBe(true);

    await act(async () => calls[0]!.reject(unavailable)); // the abandoned request failing late changes nothing
    expect(result.current).toMatchObject({ data: null, stale: true, loading: true, error: null });
    await act(async () => calls[1]!.resolve("b's data"));
    await waitFor(() => expect(result.current).toMatchObject({ data: "b's data", stale: false, error: null }));
  });

  it("pauses while the key is null", () => {
    const load = vi.fn(() => Promise.resolve("x"));
    const { result } = renderHook(() => useApiResource(null, load));
    expect(load).not.toHaveBeenCalled();
    expect(result.current).toMatchObject({ data: null, loading: false, error: null });
  });
});
