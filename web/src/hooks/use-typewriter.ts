import { useEffect, useState } from "react";

import { usePrefersReducedMotion } from "./use-media-query";

/**
 * Types `text` out at `msPerChar` (instant with reduced motion). Remount with `key={text}` to
 * restart it for a new line.
 */
export function useTypewriter(text: string, msPerChar = 25): string {
  const reduced = usePrefersReducedMotion();
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (reduced || count >= text.length) return;
    const timer = setTimeout(() => setCount((c) => c + 1), msPerChar);
    return () => clearTimeout(timer);
  }, [reduced, count, text.length, msPerChar]);

  return reduced ? text : text.slice(0, count);
}
