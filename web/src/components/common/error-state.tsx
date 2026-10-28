"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

/** A localized sentence for any failure; server messages are shown verbatim (they are facts). */
export function useErrorMessage(): (error: unknown) => string {
  const t = useTranslations("errors");
  return (error: unknown) => {
    if (error instanceof ApiError) {
      if (error.kind === "network") return t("network");
      if (error.kind === "timeout") return t("timeout");
      if (error.status === 401) return t("unauthorized");
      if (error.status === 403) return t("forbidden");
      if (error.status === 404) return t("notFound");
      return t("server", { message: error.message });
    }
    return t("server", { message: error instanceof Error ? error.message : String(error) });
  };
}

/** Failure with an optional Retry (DESIGN 4.8 error variant: `role="alert"`). */
export function ErrorState({
  title,
  error,
  message,
  onRetry,
  retrying = false,
  variant = "card",
  className,
}: {
  title?: ReactNode;
  error?: unknown;
  message?: ReactNode;
  onRetry?: () => void;
  retrying?: boolean;
  variant?: "card" | "inline";
  className?: string;
}) {
  const t = useTranslations();
  const describe = useErrorMessage();
  const body = message ?? (error !== undefined ? describe(error) : null);
  return (
    <div
      role="alert"
      className={cn(
        "text-center",
        variant === "card"
          ? "rounded-xl bg-card px-8 py-14 shadow-sm ring-1 ring-foreground/10"
          : "px-8 py-10",
        className,
      )}
    >
      <p className="text-sm font-medium text-foreground">{title ?? t("errors.title")}</p>
      {body ? <p className="mx-auto mt-1 max-w-md text-nav leading-relaxed text-destructive">{body}</p> : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry} disabled={retrying}>
          {t("common.retry")}
        </Button>
      ) : null}
    </div>
  );
}
