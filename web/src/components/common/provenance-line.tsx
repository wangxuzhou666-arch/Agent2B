"use client";

import { useTranslations } from "next-intl";

import type { Provenance } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ToolLabel, useToolLabelText } from "./tool-label";

/** "Source: Late shipments (late_shipments)": the tool by its label, then its id (DESIGN 10.4). */
export function useProvenanceSource(): (provenance: Provenance) => string {
  const t = useTranslations("provenance");
  const named = useToolLabelText();
  return (provenance) => t("source", { tool: named(provenance.label, provenance.tool) });
}

/** "15 rows · as of 2026-10-26 08:00 · scope: warehouse=WH-02" — the real as-of, never the query time. */
export function useProvenanceMeta(): (provenance: Provenance) => string {
  const t = useTranslations();
  return (provenance: Provenance) => {
    const asOf = formatDateTime(provenance.as_of);
    return [
      t("common.rows", { count: provenance.row_count }),
      asOf ? t("provenance.asOf", { time: asOf }) : t("provenance.asOfUnknown"),
      provenance.scope ? t("provenance.scope", { scope: provenance.scope }) : null,
    ]
      .filter(Boolean)
      .join(" · ");
  };
}

/**
 * Where a number came from (DESIGN 10.4): the tool's label, then its id in parentheses, then the
 * meta. `line` is the standalone sentence under an answer; `strip` is the header of a result
 * table, so the same facts are not repeated on screen.
 */
export function ProvenanceLine({
  provenance,
  variant = "line",
  className,
}: {
  provenance: Provenance;
  variant?: "line" | "strip";
  className?: string;
}) {
  const t = useTranslations("provenance");
  const common = useTranslations("common");
  const meta = useProvenanceMeta()(provenance);
  const source = useProvenanceSource()(provenance);
  if (variant === "strip") {
    return (
      <div className={cn("flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1", className)}>
        <span className="text-caption font-medium text-foreground">
          <span className="sr-only">{common("labelPrefix", { label: t("label") })}</span>
          <ToolLabel label={provenance.label} id={provenance.tool} />
        </span>
        <span className="text-caption text-faint">{meta}</span>
      </div>
    );
  }
  return (
    <p className={cn("text-xs text-faint", className)}>
      {source} · {meta}
    </p>
  );
}
