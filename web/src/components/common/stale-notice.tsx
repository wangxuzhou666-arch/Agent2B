"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Warning banner for a view that still shows the data of an earlier request because the
 * latest one failed (DESIGN 4.13 notice banner, warning tone), with an optional Retry.
 */
export function StaleNotice({
  children,
  onRetry,
  className,
}: {
  children: ReactNode;
  onRetry?: () => void;
  className?: string;
}) {
  const t = useTranslations("common");
  return (
    <div
      role="alert"
      className={cn(
        "mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning ring-1 ring-warning/30",
        className,
      )}
    >
      <span className="min-w-0 flex-1">{children}</span>
      {onRetry ? (
        <Button variant="outline" size="xs" onClick={onRetry}>
          {t("retry")}
        </Button>
      ) : null}
    </div>
  );
}
