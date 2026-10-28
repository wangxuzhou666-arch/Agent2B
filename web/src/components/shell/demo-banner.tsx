"use client";

import { TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

/** Always-on notice while the server runs in demo mode (passwordless sign-in, synthetic data). */
export function DemoBanner({ className }: { className?: string }) {
  const t = useTranslations("shell");
  return (
    <div
      role="note"
      className={cn(
        // opaque tint (the 10 % / 20 % warning wash over the page ground), so nothing shows through
        "flex items-center justify-center gap-2 border-b border-warning/30 bg-[color-mix(in_oklab,var(--warning)_10%,var(--background))] px-4 py-1.5 text-center text-xs font-medium text-warning dark:bg-[color-mix(in_oklab,var(--warning)_20%,var(--background))]",
        className,
      )}
    >
      <TriangleAlert aria-hidden="true" className="size-3.5 shrink-0 stroke-2" />
      <span>{t("demoBanner")}</span>
    </div>
  );
}
