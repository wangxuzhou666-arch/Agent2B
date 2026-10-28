"use client";

import { useTranslations } from "next-intl";

import { RouteSourceBadge } from "@/components/common/route-source-badge";
import { StatusDot, toneForStatus } from "@/components/common/status-badge";
import { useStatusLabel } from "@/components/common/status-label";
import type { AuditEntryOut } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The records of one request as a small vertical timeline, oldest first: e.g. a write's
 * `needs_confirmation` → `started` → `ok`, or a single `clarify` / `chat`. The open entry is
 * marked; any other step opens in its place.
 */
export function RequestChain({
  entries,
  currentId,
  onOpen,
}: {
  entries: readonly AuditEntryOut[];
  currentId: number;
  onOpen: (id: number) => void;
}) {
  const t = useTranslations("audit.detail");
  const statusLabel = useStatusLabel();
  return (
    <ol aria-label={t("chainTitle")} className="relative space-y-0">
      {entries.map((entry, index) => {
        const current = entry.id === currentId;
        const last = index === entries.length - 1;
        const linked = entry.parent_id !== null && entry.parent_id !== undefined;
        return (
          <li key={entry.id} className="relative flex gap-3 pb-3 last:pb-0" aria-current={current ? "step" : undefined}>
            {/* rail */}
            <span aria-hidden="true" className="relative flex w-3 shrink-0 justify-center">
              {!last ? <span className="absolute top-3 bottom-[-0.75rem] w-px bg-border" /> : null}
              <span className="relative mt-1.5 flex size-3 items-center justify-center rounded-full bg-card">
                <StatusDot tone={toneForStatus(entry.status)} className={cn("size-2", current && "ring-2 ring-ring/40")} />
              </span>
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                {current ? (
                  <span className="font-semibold text-foreground">{statusLabel(entry.status)}</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onOpen(entry.id)}
                    className="rounded-sm font-medium text-foreground underline-offset-2 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    {statusLabel(entry.status)}
                  </button>
                )}
                {entry.target ? <span className="font-mono text-xs text-muted-foreground">{entry.target}</span> : null}
                {entry.route_source ? <RouteSourceBadge source={entry.route_source} /> : null}
                {current ? <span className="text-xs text-faint">{t("thisEntry")}</span> : null}
              </div>
              <p className="mt-0.5 text-xs text-faint tabular-nums">
                {formatTimestamp(entry.ts)} · #{entry.id}
                {linked ? ` · ${t("continues", { id: entry.parent_id as number })}` : ""}
                {entry.row_count !== null && entry.row_count !== undefined
                  ? ` · ${t("rows", { count: entry.row_count })}`
                  : ""}
                {entry.problem ? ` · ${entry.problem}` : ""}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
