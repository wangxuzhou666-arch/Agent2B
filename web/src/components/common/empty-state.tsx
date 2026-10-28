import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * "Nothing here" (DESIGN 4.8). `card` is the full-page empty card; `inline` sits inside a card
 * or table that already draws the surface.
 */
export function EmptyState({
  title,
  description,
  icon: Icon,
  action,
  variant = "card",
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  icon?: LucideIcon;
  action?: ReactNode;
  variant?: "card" | "inline";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "text-center text-sm text-muted-foreground",
        variant === "card"
          ? "rounded-xl bg-card px-8 py-20 shadow-sm ring-1 ring-foreground/10"
          : "px-8 py-14",
        className,
      )}
    >
      {Icon ? (
        <Icon aria-hidden="true" className="mx-auto mb-3 size-5 stroke-[1.75] text-faint" />
      ) : null}
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? <p className="mx-auto mt-1 max-w-md text-caption text-faint">{description}</p> : null}
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}
