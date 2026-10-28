"use client";

import { ChevronDown } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { RouteSourceBadge, useRouteSourceMeaning } from "@/components/common/route-source-badge";
import { SegmentedControl } from "@/components/common/segmented-control";
import { StaleNotice } from "@/components/common/stale-notice";
import { useStatusLabel } from "@/components/common/status-label";
import { StatusDot, toneForStatus } from "@/components/common/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { htmlLang } from "@/i18n/config";
import type { AuditStatsOut } from "@/lib/api";
import { formatShare, shareRows, statusesFor, STATS_WINDOWS, type StatsWindow } from "@/lib/audit";
import { formatDateTime, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import { RouteShareChart } from "./route-share-chart";

const cardClass = "rounded-xl bg-card p-4 shadow-sm ring-1 ring-foreground/10";

function TableView({ stats, onSelectSource }: { stats: AuditStatsOut; onSelectSource: (source: string) => void }) {
  const t = useTranslations("audit.stats");
  const locale = htmlLang[useLocale()];
  const statusLabel = useStatusLabel();
  const meaningOf = useRouteSourceMeaning();
  const rows = shareRows(stats.by_route_source);
  return (
    <details className="group mt-3">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-md text-caption text-faint outline-none select-none hover:text-muted-foreground focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        {t("tableView")}
        <ChevronDown aria-hidden="true" className="size-3 stroke-2 transition-transform duration-150 group-open:rotate-180" />
      </summary>
      <div className="mt-2 overflow-x-auto rounded-lg ring-1 ring-border">
        <table className="w-full text-xs">
          <caption className="sr-only">{t("routeTitle")}</caption>
          <thead>
            <tr className="bg-muted/40 text-left text-muted-foreground">
              <th scope="col" className="px-3 py-2 font-medium">{t("colSource")}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{t("colDecisions")}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{t("colShare")}</th>
              <th scope="col" className="px-3 py-2 font-medium">{t("colOutcomes")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-border/60">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  <button
                    type="button"
                    onClick={() => onSelectSource(row.key)}
                    title={meaningOf(row.key) ?? undefined}
                    aria-label={t("filterBySource", { source: row.key })}
                    className="rounded-sm outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <RouteSourceBadge source={row.key} />
                  </button>
                </th>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatNumber(row.count, locale)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatShare(row.share, locale)}</td>
                <td className="px-3 py-1.5 text-muted-foreground tabular-nums">
                  {statusesFor(stats.buckets, row.key)
                    .map((o) => `${statusLabel(o.key)} ${formatNumber(o.count, locale)}`)
                    .join(" · ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/**
 * The stats header of the Audit window: how many decisions each routing arm made, and how
 * they ended, over a trailing window. Counts come from `/api/audit/stats` (intent records of
 * writes are excluded server-side, so a write counts once).
 */
export function AuditStats({
  stats,
  loading,
  error,
  statsWindow,
  onWindowChange,
  onRetry,
  selectedSource,
  selectedStatus,
  onSelectSource,
  onSelectStatus,
  actions,
}: {
  stats: AuditStatsOut | null;
  loading: boolean;
  error: unknown;
  statsWindow: StatsWindow;
  onWindowChange: (window: StatsWindow) => void;
  onRetry: () => void;
  selectedSource: string | null;
  selectedStatus: string | null;
  onSelectSource: (source: string) => void;
  onSelectStatus: (status: string) => void;
  /** Right end of the header row (e.g. Refresh). */
  actions?: React.ReactNode;
}) {
  const t = useTranslations("audit.stats");
  const locale = htmlLang[useLocale()];
  const statusLabel = useStatusLabel();
  const meaningOf = useRouteSourceMeaning();
  const sources = useMemo(() => (stats ? shareRows(stats.by_route_source) : []), [stats]);
  const statuses = useMemo(() => (stats ? shareRows(stats.by_status) : []), [stats]);
  const windows = STATS_WINDOWS.map((value) => ({ value, label: t(`window.${value}`) }));
  const since = stats ? formatDateTime(stats.since) : null;

  let content: React.ReactNode;
  if (!stats && error) {
    content = <ErrorState variant="card" error={error} onRetry={onRetry} />;
  } else if (!stats) {
    content = (
      <div role="status" aria-busy="true" className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <span className="sr-only">{t("loading")}</span>
        <Skeleton className="h-44 rounded-xl xl:col-span-2" />
        <Skeleton className="h-44 rounded-xl" />
      </div>
    );
  } else if (stats.total === 0) {
    content = <EmptyState title={t("emptyTitle")} description={t("emptyBody")} className="py-12" />;
  } else {
    content = (
      <div
        className={cn("grid grid-cols-1 gap-4 transition-opacity xl:grid-cols-3", (loading || Boolean(error)) && "opacity-60")}
        aria-busy={loading || undefined}
      >
        <section aria-labelledby="audit-route-title" className={cn(cardClass, "xl:col-span-2")}>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h3 id="audit-route-title" className="text-sm font-semibold text-foreground">
              {t("routeTitle")}
            </h3>
            <p className="text-caption text-faint">{t("routeHint")}</p>
          </div>
          <RouteShareChart
            rows={sources}
            buckets={stats.buckets}
            selected={selectedSource}
            onSelect={onSelectSource}
            meaningOf={meaningOf}
          />
          <TableView stats={stats} onSelectSource={onSelectSource} />
          <p className="mt-2 text-caption text-faint">{t("countsNote")}</p>
        </section>
        <section aria-labelledby="audit-status-title" className={cardClass}>
          <h3 id="audit-status-title" className="mb-3 text-sm font-semibold text-foreground">
            {t("statusTitle")}
          </h3>
          <ul className="space-y-0.5">
            {statuses.map((row) => {
              const active = selectedStatus === row.key;
              return (
                <li key={row.key}>
                  <button
                    type="button"
                    onClick={() => onSelectStatus(row.key)}
                    aria-pressed={active}
                    data-status={row.key}
                    data-touch-target
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50",
                      active ? "bg-accent text-accent-foreground" : "hover:bg-muted/60",
                    )}
                  >
                    <StatusDot tone={toneForStatus(row.key)} className="size-2" />
                    <span className="min-w-0 flex-1 truncate">{statusLabel(row.key)}</span>
                    <span className="font-medium tabular-nums">{formatNumber(row.count, locale)}</span>
                    <span
                      className={cn(
                        "w-10 text-right text-xs tabular-nums",
                        active ? "text-muted-foreground" : "text-faint",
                      )}
                    >
                      {formatShare(row.share, locale)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    );
  }

  return (
    <section aria-labelledby="audit-stats-title" className="mb-8">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 id="audit-stats-title" className="text-base font-semibold text-foreground">
          {t("title")}
        </h2>
        <SegmentedControl label={t("windowLabel")} options={windows} value={statsWindow} onChange={onWindowChange} />
        {stats ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {t.rich("summary", {
              count: stats.total,
              n: (chunks) => <span className="font-medium text-foreground tabular-nums">{chunks}</span>,
              since: since ?? "—",
            })}
          </p>
        ) : null}
        {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
      </div>
      {/* the last request failed: the figures below belong to the window before the change */}
      {stats && error ? <StaleNotice onRetry={onRetry}>{t("stale")}</StaleNotice> : null}
      {content}
    </section>
  );
}
