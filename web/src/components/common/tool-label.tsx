"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

/** A display label worth showing next to `id`: present, and not the id itself. */
function shownLabel(label: string | null | undefined, id: string | null | undefined): string | null {
  const text = label?.trim() ?? "";
  return text !== "" && text !== id ? text : null;
}

/**
 * "Label (tool_id)" as plain text (DESIGN 10.4), for captions, region names and sentences: the
 * human label first, the id the audit log records second. Only the id when there is no label,
 * only the label when there is no id.
 */
export function useToolLabelText(): (label: string | null | undefined, id: string | null | undefined) => string {
  const t = useTranslations("common");
  return (label, id) => {
    const text = shownLabel(label, id);
    if (!id) return text ?? "";
    if (!text) return id;
    return t.markup("labelWithId", { label: text, id, mono: (chunks) => chunks });
  };
}

/**
 * "Create purchase order (create_po)": the label, then the id in mono. `tone`: `faint` for the
 * id on a plain ground; `inherit` where the text changes colour (a chip that inverts on hover).
 */
export function ToolLabel({
  label,
  id,
  tone = "faint",
  className,
}: {
  label?: string | null;
  id?: string | null;
  tone?: "faint" | "inherit";
  className?: string;
}) {
  const t = useTranslations("common");
  const text = shownLabel(label, id);
  const idClass = cn("font-mono text-xs font-normal break-all", tone === "faint" ? "text-faint" : "opacity-70");
  if (!id) return text ? <span className={className}>{text}</span> : null;
  if (!text) return <span className={cn("font-mono break-all", className)}>{id}</span>;
  return (
    <span className={className}>
      {t.rich("labelWithId", { label: text, id, mono: (chunks) => <span className={idClass}>{chunks}</span> })}
    </span>
  );
}
