import { cn } from "@/lib/utils";

/** Three monochrome dots in the current text colour: the only "thinking" indicator (DESIGN 6.3). */
export function ThinkingDots({ className }: { className?: string }) {
  return (
    <span className={cn("ai-dots", className)} aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
