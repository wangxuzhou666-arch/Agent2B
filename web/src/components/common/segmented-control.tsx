"use client";

import { cn } from "@/lib/utils";

export interface SegmentedOption<V extends string> {
  value: V;
  label: string;
}

/**
 * A small single-choice button group (e.g. the 24 h / 7 d / 30 d window). Each option is a
 * toggle button with `aria-pressed`, so the choice reads out without a custom widget role.
 */
export function SegmentedControl<V extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
  className,
}: {
  label: string;
  options: readonly SegmentedOption<V>[];
  value: V;
  onChange: (value: V) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn("inline-flex h-8 items-center rounded-lg bg-muted p-[3px] text-muted-foreground", className)}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            data-touch-target
            className={cn(
              "h-full rounded-md px-2.5 text-sm font-medium tabular-nums whitespace-nowrap transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50",
              active
                ? "bg-background text-foreground shadow-sm dark:bg-input/30"
                : "text-foreground/60 hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
