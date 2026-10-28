import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type Tone = "success" | "warning" | "destructive" | "info" | "neutral";

const TONE_BY_STATUS: Record<string, Tone> = {
  ok: "success",
  approved: "success",
  started: "neutral",
  skipped: "neutral",
  expired: "neutral",
  empty: "neutral",
  needs_confirmation: "warning",
  pending: "warning",
  invalid: "warning",
  clarify: "warning",
  chat: "neutral",
  blocked: "warning",
  rejected: "destructive",
  error: "destructive",
};

/**
 * Tone for a status string from any endpoint (trace, audit, approval). A guard that blocked
 * a request is a warning (the control worked); only failures and rejections are destructive.
 */
export function toneForStatus(status: string): Tone {
  return TONE_BY_STATUS[status] ?? "neutral";
}

const DOT: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground",
};

/** Tone pill (DESIGN 4.12): soft semantic fill, never a raw palette colour. */
export function StatusBadge({
  tone,
  children,
  dot = false,
  className,
}: {
  tone: Tone;
  children: ReactNode;
  dot?: boolean;
  className?: string;
}) {
  return (
    <Badge variant={tone} className={cn("rounded-full", className)}>
      {dot ? <span aria-hidden="true" className={cn("size-1.5 rounded-full", DOT[tone])} /> : null}
      {children}
    </Badge>
  );
}

export function StatusDot({ tone, className }: { tone: Tone; className?: string }) {
  return <span aria-hidden="true" className={cn("inline-block size-1.5 rounded-full", DOT[tone], className)} />;
}
