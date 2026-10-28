"use client";

import { BotOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useApiResource } from "@/hooks/use-api-resource";
import { api, type ProviderInfo } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * A quiet neutral pill (DESIGN 4.12) while the server answers without a language model: no API
 * key, so its mock provider stands in. Routing then uses rules and keyword search and every answer
 * is template text, which the tooltip (and, for screen readers, the description) says. Nothing
 * when a model is configured, or before the server has said which.
 */
export function ModelIndicator({ provider, className }: { provider: ProviderInfo | null | undefined; className?: string }) {
  const t = useTranslations("shell");
  const hintId = useId();
  if (provider?.mock !== true) return null;
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-describedby={hintId}
            data-provider={provider.name}
            className={cn(
              "inline-flex h-6 shrink-0 cursor-default items-center gap-1.5 rounded-full bg-muted px-2.5 text-xs font-medium whitespace-nowrap text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
              className,
            )}
          >
            <BotOff aria-hidden="true" className="size-3.5 stroke-[1.75]" />
            {t("noModel")}
          </button>
        </TooltipTrigger>
        <TooltipContent>{t("noModelHint")}</TooltipContent>
      </Tooltip>
      {/* the same words for screen readers, whether or not the tooltip is open */}
      <span id={hintId} className="sr-only">
        {t("noModelHint")}
      </span>
    </>
  );
}

/** The header's indicator: which model the server runs, read once per console session. */
export function ConsoleModelIndicator({ className }: { className?: string }) {
  const health = useApiResource("health", (signal) => api.health(signal));
  return <ModelIndicator provider={health.data?.provider} className={className} />;
}
