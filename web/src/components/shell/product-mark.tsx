import { cn } from "@/lib/utils";

/**
 * Monochrome mark: two parallel tracks. The solid one is code (it computes); the light one is
 * the model (it routes and narrates).
 */
export function ProductMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" className={cn("size-8 text-foreground", className)}>
      <rect x="9" y="6" width="4.5" height="20" rx="2.25" fill="currentColor" />
      <rect x="18.5" y="6" width="4.5" height="20" rx="2.25" fill="currentColor" opacity="0.4" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-serif text-lg font-bold tracking-tight text-foreground", className)}>
      twotrack
    </span>
  );
}
