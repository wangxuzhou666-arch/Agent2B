"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import type { Deadline } from "@/lib/approvals";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface CountdownState {
  /** null until the first tick (the clock is only read in the browser, after render). */
  remainingMs: number | null;
  /** remaining / window, 1 → 0. */
  fraction: number;
  /** The deadline has passed on this page's clock. For display only: the server decides. */
  ended: boolean;
}

/**
 * Time left until `deadline`, ticking once a second on the monotonic clock (DESIGN 10.6). The
 * deadline was fixed when the confirmation arrived, from the time left the server sent (see
 * `deadlineIn` / `listedDeadline`), so nothing here reads the wall clock.
 */
export function useCountdown({ endsAt, windowMs }: Deadline): CountdownState {
  const key = `${endsAt}|${windowMs ?? ""}`;
  const [clock, setClock] = useState<{ key: string; remaining: number } | null>(null);

  useEffect(() => {
    const tick = () => {
      const remaining = Math.max(0, endsAt - performance.now());
      setClock({ key, remaining });
      if (remaining <= 0) clearInterval(interval);
    };
    const interval = setInterval(tick, 1000);
    const first = setTimeout(tick, 0);
    return () => {
      clearTimeout(first);
      clearInterval(interval);
    };
  }, [endsAt, key]);

  const current = clock?.key === key ? clock : null;
  if (!current) return { remainingMs: null, fraction: 1, ended: false };
  return {
    remainingMs: current.remaining,
    fraction: windowMs && windowMs > 0 ? Math.min(1, current.remaining / windowMs) : 0,
    ended: current.remaining <= 0,
  };
}

/**
 * "Expires in 4:59", amber in the last 20 % of the window, with an optional bar. It never ends
 * anything: `onEnd` lets the card ask the server, whose 410 (or list) is the verdict.
 */
export function Countdown({
  deadline,
  onEnd,
  showBar = true,
  className,
}: {
  deadline: Deadline;
  onEnd?: () => void;
  showBar?: boolean;
  className?: string;
}) {
  const t = useTranslations("approval");
  const { remainingMs, fraction, ended } = useCountdown(deadline);
  const urgent = remainingMs !== null && fraction <= 0.2;

  const onEndRef = useRef(onEnd);
  useEffect(() => {
    onEndRef.current = onEnd;
  }, [onEnd]);
  useEffect(() => {
    if (ended) onEndRef.current?.();
  }, [ended]);

  return (
    <div className={cn("flex min-w-0 items-center gap-2", className)}>
      <span className={cn("text-caption tabular-nums", urgent ? "text-warning" : "text-faint")}>
        {t("expiresIn", { time: remainingMs === null ? "–:––" : formatClock(remainingMs) })}
      </span>
      {showBar ? (
        <span aria-hidden="true" className="h-1 w-16 overflow-hidden rounded-full bg-foreground/10">
          <span
            className={cn(
              "block h-full origin-left rounded-full transition-transform duration-1000 ease-linear",
              urgent ? "bg-warning" : "bg-foreground/40",
            )}
            style={{ transform: `scaleX(${fraction})` }}
          />
        </span>
      ) : null}
    </div>
  );
}
