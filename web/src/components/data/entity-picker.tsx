"use client";

import { useTranslations } from "next-intl";

import type { EntityOut } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Root entities, from the spec. A rail with descriptions on wide screens, a row of scrollable
 * pills on narrow ones (same buttons, so keyboard order and names do not change).
 */
export function EntityPicker({
  entities,
  selected,
  onSelect,
}: {
  entities: readonly EntityOut[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const t = useTranslations("data");
  return (
    <nav aria-label={t("entities")} className="min-w-0">
      <p className="mb-1.5 hidden px-3 text-caption font-medium tracking-wide text-muted-foreground lg:block">
        {t("entities")}
      </p>
      <ul className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 lg:mx-0 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:px-0 lg:pb-0">
        {entities.map((entity) => {
          const active = entity.id === selected;
          return (
            <li key={entity.id} className="shrink-0">
              <button
                type="button"
                onClick={() => onSelect(entity.id)}
                aria-current={active ? "page" : undefined}
                data-touch-target
                className={cn(
                  "w-full rounded-full border px-3 py-1.5 text-left text-sm whitespace-nowrap transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 lg:rounded-md lg:border-transparent lg:px-3 lg:py-2 lg:whitespace-normal",
                  active
                    ? "border-primary/30 bg-accent font-medium text-accent-foreground"
                    : "border-border bg-card text-foreground/80 hover:bg-muted/60 hover:text-foreground lg:bg-transparent",
                )}
              >
                <span className="block">{entity.label}</span>
                {entity.description ? (
                  <span
                    className={cn(
                      "mt-0.5 hidden text-xs leading-snug lg:line-clamp-2",
                      active ? "text-muted-foreground" : "text-faint",
                    )}
                  >
                    {entity.description}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
