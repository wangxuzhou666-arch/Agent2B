"use client";

import { Check } from "lucide-react";
import { useTranslations } from "next-intl";
import { Fragment } from "react";

import { IMPORT_STEPS, stepIndex, type ImportStep } from "@/lib/imports";
import { cn } from "@/lib/utils";

/** "1 Upload · 2 Preview · 3 Confirm · 4 Done" (DESIGN 10.9): current ink, done green with a check, pending faint. */
export function ImportSteps({ current }: { current: ImportStep }) {
  const t = useTranslations("import.steps");
  const at = stepIndex(current);
  return (
    <ol aria-label={t("label")} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption">
      {IMPORT_STEPS.map((step, index) => {
        const done = index < at || (current === "done" && step === "done");
        const active = index === at && current !== "done";
        return (
          <Fragment key={step}>
            {index > 0 ? (
              <li aria-hidden="true" className="text-faint">
                ·
              </li>
            ) : null}
            <li
              aria-current={active ? "step" : undefined}
              className={cn(
                "inline-flex items-center gap-1",
                active ? "font-medium text-foreground" : done ? "text-success" : "text-faint",
              )}
            >
              {done ? <Check aria-hidden="true" className="size-3.5 stroke-[2.5]" /> : <span className="tabular-nums">{index + 1}</span>}
              <span>{t(step)}</span>
              {done ? <span className="sr-only"> ({t("completed")})</span> : null}
            </li>
          </Fragment>
        );
      })}
    </ol>
  );
}
