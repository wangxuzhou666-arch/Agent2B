"use client";

import { Lock } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { htmlLang } from "@/i18n/config";
import type { FieldOut } from "@/lib/api";
import { unitSuffix } from "@/lib/entities";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export const EMPTY_VALUE = <span className="text-faint">—</span>;

/** A stored value rendered by its declared type (DESIGN 10.8 column type → rendering). */
export function FieldValue({ field, value }: { field: Pick<FieldOut, "type">; value: unknown }) {
  const t = useTranslations("data");
  const locale = htmlLang[useLocale()];
  if (value === null || value === undefined || value === "") return EMPTY_VALUE;
  switch (field.type) {
    case "enum":
      return (
        <Badge variant="outline" className="font-normal">
          {String(value)}
        </Badge>
      );
    case "bool": {
      const on = value === true || value === "true";
      return (
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className={cn("size-1.5 rounded-full", on ? "bg-success" : "bg-muted-foreground opacity-50")} />
          <span className="text-muted-foreground">{on ? t("yes") : t("no")}</span>
        </span>
      );
    }
    case "int":
    case "float":
      return typeof value === "number" ? <>{formatNumber(value, locale)}</> : <>{String(value)}</>;
    case "date":
      return <span className="tabular-nums">{String(value)}</span>;
    default:
      if (typeof value === "object") {
        return <code className="font-mono text-xs text-muted-foreground">{JSON.stringify(value)}</code>;
      }
      return <>{String(value)}</>;
  }
}

/**
 * Marker for a restricted field the role may see (a reminder that it is sensitive). Inside a
 * button (a sortable header) it must not be focusable itself: `inline` keeps it a plain icon.
 */
export function RestrictedMark({ className, inline = false }: { className?: string; inline?: boolean }) {
  const t = useTranslations("data");
  if (inline) {
    return (
      <span title={t("restrictedHint")} className={cn("inline-flex text-warning", className)}>
        <Lock aria-hidden="true" className="size-3" />
        <span className="sr-only">{t("restricted")}</span>
      </span>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          aria-label={t("restricted")}
          className={cn("inline-flex rounded-sm text-warning outline-none focus-visible:ring-3 focus-visible:ring-ring/50", className)}
        >
          <Lock aria-hidden="true" className="size-3" />
        </span>
      </TooltipTrigger>
      <TooltipContent>{t("restrictedHint")}</TooltipContent>
    </Tooltip>
  );
}

/** Column header text: label, unit (unless the label already names it), sensitivity. */
export function FieldHeader({ field }: { field: Pick<FieldOut, "label" | "unit" | "sensitivity"> }) {
  const unit = unitSuffix(field);
  return (
    <span className="inline-flex items-center gap-1">
      <span>{field.label}</span>
      {unit ? <span className="font-normal text-faint">({unit})</span> : null}
      {field.sensitivity === "restricted" ? <RestrictedMark inline /> : null}
    </span>
  );
}

/** Plain-text header for sort buttons and titles. */
export function headerText(field: Pick<FieldOut, "label" | "unit">): string {
  const unit = unitSuffix(field);
  return unit ? `${field.label} (${unit})` : field.label;
}
