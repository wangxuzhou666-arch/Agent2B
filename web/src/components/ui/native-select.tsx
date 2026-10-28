import { ChevronDown } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A native `<select>` dressed as the select trigger (DESIGN 4.3): keyboard, screen-reader and
 * mobile pickers come from the platform. `size="sm"` is the 28 px filter-bar height.
 */
function NativeSelect({
  className,
  size = "default",
  ...props
}: Omit<React.ComponentProps<"select">, "size"> & { size?: "default" | "sm" }) {
  return (
    <span data-slot="native-select-wrapper" className={cn("relative inline-flex min-w-0", className)}>
      <select
        data-slot="native-select"
        data-size={size}
        className={cn(
          "w-full min-w-0 cursor-pointer appearance-none truncate rounded-lg border border-input bg-input-background pr-8 pl-2.5 text-base transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30",
          size === "sm" ? "h-7" : "h-8",
        )}
        {...props}
      />
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 right-2 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </span>
  );
}

export { NativeSelect };
