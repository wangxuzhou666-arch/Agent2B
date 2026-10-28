"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

import { StatusBadge, toneForStatus } from "./status-badge";

/** Localized name of a status from any endpoint; a status this build does not know stays verbatim. */
export function useStatusLabel(): (status: string) => string {
  const t = useTranslations("status");
  return (status: string) => {
    const key = status as Parameters<typeof t>[0];
    return t.has(key) ? t(key) : status;
  };
}

/** Tone pill for an audit / trace / approval status, with the raw value kept for tests and tooling. */
export function StatusPill({ status, dot = true, className }: { status: string; dot?: boolean; className?: string }) {
  const label = useStatusLabel();
  return (
    <StatusBadge tone={toneForStatus(status)} dot={dot} className={cn(className)}>
      <span data-status={status}>{label(status)}</span>
    </StatusBadge>
  );
}
