"use client";

import { X } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

export interface FilterChip {
  id: string;
  /** The dimension, e.g. "Status". */
  label: string;
  /** The value, e.g. "Needs approval". */
  value: string;
  /** What the value means, when it is an identifier (shown on hover). */
  hint?: string;
  onRemove: () => void;
}

/** Active-filter chip row (DESIGN 4.11): "Filtered" · dimension + value chips · Clear all. */
export function FilterChips({ chips, onClearAll }: { chips: readonly FilterChip[]; onClearAll: () => void }) {
  const t = useTranslations("filters");
  if (chips.length === 0) return null;
  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-border/60 pt-2.5">
      <span className="text-caption text-faint">{t("filtered")}</span>
      <ul className="contents">
        {chips.map((chip) => (
          <li
            key={chip.id}
            className="inline-flex h-6 max-w-full items-center gap-1 rounded-full border border-border bg-secondary pr-1 pl-2.5 text-xs"
          >
            <span className="shrink-0 text-faint">{chip.label}</span>
            <span className="min-w-0 truncate font-medium text-foreground" title={chip.hint ? `${chip.value} · ${chip.hint}` : chip.value}>
              {chip.value}
            </span>
            <button
              type="button"
              onClick={chip.onRemove}
              aria-label={t("remove", { filter: `${chip.label} ${chip.value}` })}
              className="relative flex size-4 shrink-0 items-center justify-center rounded-full text-faint outline-none after:absolute after:-inset-1.5 hover:bg-foreground/10 hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <X aria-hidden="true" className="size-3" />
            </button>
          </li>
        ))}
      </ul>
      {chips.length > 1 ? (
        <Button variant="ghost" size="xs" className="h-6 px-2 text-xs" onClick={onClearAll}>
          {t("clearAll")}
        </Button>
      ) : null}
    </div>
  );
}
