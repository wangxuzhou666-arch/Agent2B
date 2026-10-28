import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Serif page title, description, optional caption and a right-aligned action slot (DESIGN 4.9). */
export function PageHeader({
  title,
  description,
  caption,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  caption?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-6 flex items-start justify-between", className)}>
      <div className="min-w-0">
        <h1 className="font-serif text-2xl leading-tight font-semibold tracking-tight text-foreground">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 text-nav leading-relaxed text-muted-foreground">{description}</p>
        ) : null}
        {caption ? <p className="mt-0.5 text-xs leading-relaxed text-faint">{caption}</p> : null}
      </div>
      {actions ? <div className="ml-6 flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
