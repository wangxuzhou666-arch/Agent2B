"use client";

import { ChevronDown } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ThinkingDots } from "@/components/common/thinking-dots";
import { useTypewriter } from "@/hooks/use-typewriter";
import type { TraceStep } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { activeLabel, describeTrace, type TraceLine, type TraceTone, type TraceWords } from "@/lib/trace";
import { cn } from "@/lib/utils";

import { RouteSourceBadge } from "./route-source-badge";

const DOT: Record<TraceTone, string> = {
  ok: "bg-success",
  empty: "bg-current opacity-70",
  warn: "bg-warning",
  error: "bg-destructive",
  neutral: "bg-current opacity-40",
};

/** Live view keeps the newest lines; older ones fold into "…N earlier steps" (DESIGN 10.3). */
const LIVE_MAX_LINES = 5;
const PATIENCE_AFTER_MS = 20_000;

/** Fixed mark column (= width of the dots) so text never shifts when a dot becomes a line. */
function Mark({ children }: { children: ReactNode }) {
  return <span className="flex h-[21px] w-[18px] shrink-0 items-center justify-center">{children}</span>;
}

export function TraceRow({ line, className }: { line: TraceLine; className?: string }) {
  const t = useTranslations("trace.step");
  const duration = formatDuration(line.durationMs);
  return (
    <li className={cn("flex items-start gap-2.5 text-caption leading-relaxed font-normal", className)}>
      <Mark>
        <span aria-hidden="true" className={cn("size-1.5 rounded-full", DOT[line.tone])} />
      </Mark>
      <span className="min-w-0 flex-1 break-words">
        {t(line.key, line.values)}
        {line.note ? <span className="text-faint"> · {line.note}</span> : null}
      </span>
      {line.source ? <RouteSourceBadge source={line.source} className="mt-px" /> : null}
      {duration ? <span className="shrink-0 tabular-nums text-faint">{duration}</span> : null}
    </li>
  );
}

function ActiveLine({ steps }: { steps: readonly TraceStep[] }) {
  const t = useTranslations("trace.active");
  const label = activeLabel(steps);
  const text = t(label.key, label.values);
  return (
    <li className="ai-enter flex items-start gap-2.5 text-caption leading-relaxed text-foreground">
      <Mark>
        <ThinkingDots />
      </Mark>
      <Typewriter key={text} text={text} />
    </li>
  );
}

function Typewriter({ text }: { text: string }) {
  const shown = useTypewriter(text);
  return (
    <span className="min-w-0 flex-1">
      <span aria-hidden="true">{shown}</span>
      <span className="sr-only">{text}</span>
    </span>
  );
}

/** Lightness ages completed lines: newest muted, next two faint, older fainter. */
function ageClass(fromNewest: number): string {
  if (fromNewest === 0) return "text-muted-foreground";
  if (fromNewest <= 2) return "text-faint";
  return "text-faint opacity-60";
}

export interface TraceListProps {
  steps: readonly TraceStep[];
  /** The request is still running: show lines as they arrive plus the thinking line. */
  live?: boolean;
  /** `performance.now()` at the start of the request, for the slow-tool hint. */
  startedAt?: number;
  /** Client-measured wall time of the request, for "Thought for 0.4 s". */
  elapsedMs?: number | null;
  outcome?: "done" | "stopped" | "failed";
  /** `approval`: the run an approval released ("Ran after approval in …"). */
  kind?: "query" | "approval";
  defaultOpen?: boolean;
  className?: string;
}

/**
 * The reasoning trace of one request. Only what the backend sent is shown: each step's
 * humanized label, its route-source tag and its measured duration.
 */
export function TraceList({
  steps,
  live = false,
  startedAt,
  elapsedMs = null,
  outcome = "done",
  kind = "query",
  defaultOpen = false,
  className,
}: TraceListProps) {
  const t = useTranslations("trace");
  // named scope items ("not packaging", "A、B") read in the current language
  const words = useMemo<TraceWords>(
    () => ({ not: (item) => t("notItem", { item }), join: (items) => items.join(t("itemSeparator")) }),
    [t],
  );
  const lines = useMemo(() => describeTrace(steps, words), [steps, words]);
  const [slowSince, setSlowSince] = useState<number | null>(null);

  useEffect(() => {
    if (!live) return;
    const timer = setTimeout(() => setSlowSince(startedAt ?? 0), PATIENCE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [live, startedAt]);

  if (live) {
    const visible = lines.slice(-LIVE_MAX_LINES);
    const hidden = lines.length - visible.length;
    const slow = slowSince !== null && slowSince === (startedAt ?? 0);
    return (
      <div className={className} aria-live="off">
        <ol aria-label={t("label")} className="space-y-0.5">
          {hidden > 0 ? (
            <li className="pl-[28px] text-caption text-faint">{t("earlier", { count: hidden })}</li>
          ) : null}
          {visible.map((line, i) => (
            <TraceRow
              key={line.id}
              line={line}
              className={cn("ai-enter transition-colors duration-240", ageClass(visible.length - 1 - i))}
            />
          ))}
          <ActiveLine steps={steps} />
        </ol>
        {slow ? <p className="mt-1 pl-[28px] text-caption text-faint">{t("patience")}</p> : null}
      </div>
    );
  }

  // same units as the step durations: "45 ms", "3.4 s", "12 s"
  const duration = elapsedMs !== null ? (formatDuration(elapsedMs) ?? "<1 ms") : null;
  const summary =
    duration === null
      ? t("summarySteps", { count: steps.length })
      : outcome === "failed"
        ? t("summaryFailed", { duration })
        : outcome === "stopped"
          ? t("summaryStopped", { duration })
          : kind === "approval"
            ? t("summaryApproved", { duration })
            : t("summary", { duration });

  return (
    <details className={cn("group", className)} open={defaultOpen || undefined}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-2.5 rounded-md text-caption text-faint outline-none select-none hover:text-muted-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
        <Mark>
          <span aria-hidden="true" className="size-1.5 rounded-full bg-current opacity-70" />
        </Mark>
        <span className="tabular-nums">{summary}</span>
        <ChevronDown
          aria-hidden="true"
          className="size-3 stroke-2 transition-transform duration-150 group-open:rotate-180"
        />
      </summary>
      {lines.length > 0 ? (
        <ol aria-label={t("label")} className="mt-1 space-y-0.5 text-muted-foreground">
          {lines.map((line) => (
            <TraceRow key={line.id} line={line} />
          ))}
        </ol>
      ) : null}
    </details>
  );
}
