"use client";

import { useTranslations } from "next-intl";
import { useId } from "react";

import { ToolLabel } from "@/components/common/tool-label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ClarifyOption } from "@/lib/api";
import { cn } from "@/lib/utils";

const chipClass = cn(
  "inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-accent px-3.5 py-1.5 text-sm font-medium text-accent-foreground transition-colors outline-none",
  "hover:bg-primary hover:text-primary-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-40",
);

function Chip({
  option,
  interactive,
  onPick,
}: {
  option: ClarifyOption;
  interactive: boolean;
  onPick: (option: ClarifyOption) => void;
}) {
  const t = useTranslations("hub");
  const hintId = useId();
  const unfiltered = option.unfiltered === true;
  const button = (
    <button
      type="button"
      disabled={!interactive}
      onClick={() => onPick(option)}
      // an unfiltered choice explains itself in a tooltip instead (one tooltip, not two)
      title={unfiltered ? undefined : option.description}
      aria-describedby={unfiltered ? hintId : undefined}
      data-touch-target
      data-unfiltered={unfiltered || undefined}
      className={chipClass}
    >
      {/* what the choice is, then the report it runs; the id takes the chip's colour, so it stays legible when the chip inverts on hover */}
      <ToolLabel label={option.label} id={option.name} tone="inherit" />
      {unfiltered ? (
        <span className="rounded-sm bg-muted px-1.5 text-xs leading-5 font-normal text-muted-foreground">
          {t("clarifyUnfiltered")}
        </span>
      ) : null}
      {option.effect === "write" ? (
        <span className="text-xs font-normal opacity-80">· {t("clarifyWrites")}</span>
      ) : null}
    </button>
  );
  if (!unfiltered) return button;
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        <TooltipContent>{t("clarifyUnfilteredHint")}</TooltipContent>
      </Tooltip>
      {/* the same words for screen readers, whether or not the tooltip is open */}
      <span id={hintId} className="sr-only">
        {t("clarifyUnfilteredHint")}
      </span>
    </>
  );
}

/**
 * Candidate reports when routing was ambiguous, or the scope guard's choices when the routed
 * report cannot apply a constraint the question names (DESIGN 10.5). Picking one re-sends the
 * same question with `explicit_target` and the option's `explicit_params`, untouched, so the run
 * is attributed to the `explicit` source. A choice that runs without the asked-for filter is
 * tagged "Unfiltered". Interactive only on the latest turn and while nothing else is running.
 */
export function ClarifyChips({
  options,
  interactive,
  onPick,
}: {
  options: readonly ClarifyOption[];
  interactive: boolean;
  onPick: (option: ClarifyOption) => void;
}) {
  const t = useTranslations("hub");
  return (
    <div className="space-y-2">
      <div role="group" aria-label={t("clarifyHint")} className="flex flex-wrap gap-2">
        {options.map((option, index) => (
          // one report may be offered several times with different params (a scope conflict)
          <Chip key={`${index}:${option.name}`} option={option} interactive={interactive} onPick={onPick} />
        ))}
      </div>
      {interactive ? <p className="text-caption text-faint">{t("clarifyHint")}</p> : null}
    </div>
  );
}
