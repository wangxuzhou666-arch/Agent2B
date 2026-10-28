"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

/**
 * The meaning of a route source in the current language (`fallback` → "Chat fallback: no
 * report matched"), for titles, tooltips and filter options; null for a value this build does
 * not know (it is still shown, verbatim).
 */
export function useRouteSourceMeaning(): (source: string) => string | null {
  const t = useTranslations("routeSource");
  return (source: string) => {
    const key = source as Parameters<typeof t>[0];
    return t.has(key) ? t(key) : null;
  };
}

/**
 * Which routing arm decided: `rule`, `explicit`, `llm`, `llm_confirm`, `semantic`, `agent`,
 * `mcp`, `fallback`… The value is shown verbatim (it is what the audit log records); its meaning
 * goes in the title and in screen-reader text. Values this build does not know are still shown.
 */
export function RouteSourceBadge({ source, className }: { source: string; className?: string }) {
  const meaning = useRouteSourceMeaning()(source);
  return (
    <span
      title={meaning ?? undefined}
      data-route-source={source}
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-sm bg-muted px-1.5 font-mono text-xs leading-none text-muted-foreground",
        className,
      )}
    >
      {source}
      {meaning ? <span className="sr-only"> ({meaning})</span> : null}
    </span>
  );
}
