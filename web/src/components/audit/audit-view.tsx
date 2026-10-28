"use client";

import { RefreshCw } from "lucide-react";
import type { Route } from "next";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useMemo, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { StaleNotice } from "@/components/common/stale-notice";
import { Button } from "@/components/ui/button";
import { useApiResource } from "@/hooks/use-api-resource";
import { api } from "@/lib/api";
import {
  activeFilterCount,
  AUDIT_FILTER_KEYS,
  AUDIT_STATUSES,
  auditQueryFor,
  DEFAULT_STATS_WINDOW,
  readAuditFilters,
  ROUTE_SOURCES,
  UNKNOWN_ROUTE_SOURCE,
  windowSince,
  withExtras,
  type AuditFilters,
  type StatsWindow,
} from "@/lib/audit";
import { AUDIT_PAGE_SIZE } from "@/lib/config";
import { cn } from "@/lib/utils";

import { AuditDetailSheet } from "./audit-detail";
import { AuditFilterBar } from "./audit-filters";
import { AuditStats } from "./audit-stats";
import { AuditTable } from "./audit-table";

function positiveInt(raw: string | null): number | null {
  const n = Number.parseInt(raw ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * The Audit window: routing / outcome stats over a trailing window, then the filterable log,
 * and a replay sheet for one entry. Filters, page and the open entry live in the URL, so a
 * link from Approvals or Import (`/audit?id=42`, `/audit?grant_id=…`) lands on the record.
 */
export function AuditView() {
  const t = useTranslations("audit");
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const filters = useMemo(() => readAuditFilters(new URLSearchParams(params.toString())), [params]);
  const page = positiveInt(params.get("page")) ?? 1;
  const openId = positiveInt(params.get("id"));
  const [statsWindow, setStatsWindow] = useState<StatsWindow>(DEFAULT_STATS_WINDOW);

  const stats = useApiResource(`stats:${statsWindow}`, (signal) =>
    api.auditStats({ since: windowSince(statsWindow) }, signal),
  );
  const listKey = JSON.stringify([filters, page]);
  const list = useApiResource(`list:${listKey}`, (signal) =>
    api.listAudit(auditQueryFor(filters, page, AUDIT_PAGE_SIZE), signal),
  );

  const navigate = useCallback(
    (next: { filters?: AuditFilters; page?: number; id?: number | null }) => {
      const nextFilters = next.filters ?? filters;
      const search = new URLSearchParams();
      for (const key of AUDIT_FILTER_KEYS) {
        const value = nextFilters[key];
        if (value) search.set(key, value);
      }
      const nextPage = next.page ?? (next.filters ? 1 : page);
      if (nextPage > 1) search.set("page", String(nextPage));
      const id = next.id === undefined ? openId : next.id;
      if (id) search.set("id", String(id));
      const qs = search.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ""}` as Route, { scroll: false });
    },
    [filters, page, openId, pathname, router],
  );

  const toggle = (key: "route_source" | "status", value: string) => {
    const next: AuditFilters = { ...filters };
    if (next[key] === value) delete next[key];
    else next[key] = value;
    navigate({ filters: next });
  };

  const statusOptions = withExtras(AUDIT_STATUSES, [
    ...Object.keys(stats.data?.by_status ?? {}),
    ...(filters.status ? [filters.status] : []),
  ]);
  const sourceOptions = withExtras(
    ROUTE_SOURCES,
    [...Object.keys(stats.data?.by_route_source ?? {}), ...(filters.route_source ? [filters.route_source] : [])].filter(
      (s) => s !== UNKNOWN_ROUTE_SOURCE,
    ),
  );

  const active = activeFilterCount(filters);
  const data = list.data;
  const refresh = () => {
    stats.reload();
    list.reload();
  };

  return (
    <>
      <AuditStats
        stats={stats.data}
        loading={stats.loading}
        error={stats.error}
        statsWindow={statsWindow}
        onWindowChange={setStatsWindow}
        onRetry={stats.reload}
        selectedSource={filters.route_source ?? null}
        selectedStatus={filters.status ?? null}
        onSelectSource={(source) => toggle("route_source", source)}
        onSelectStatus={(status) => toggle("status", status)}
        actions={
          <Button variant="outline" size="sm" onClick={refresh} aria-label={t("refresh")}>
            <RefreshCw aria-hidden="true" />
            <span className="hidden sm:inline">{t("refresh")}</span>
          </Button>
        }
      />

      <section aria-labelledby="audit-log-title">
        <h2 id="audit-log-title" className="mb-3 text-base font-semibold text-foreground">
          {t("logTitle")}
        </h2>
        <AuditFilterBar
          filters={filters}
          onChange={(next) => navigate({ filters: next })}
          statusOptions={statusOptions}
          sourceOptions={sourceOptions}
        />
        {list.error && !data ? (
          <ErrorState error={list.error} onRetry={list.reload} />
        ) : (
          <>
            {/* the last request failed: what is shown belongs to the filters before the change */}
            {list.error ? <StaleNotice onRetry={list.reload}>{t("staleList")}</StaleNotice> : null}
            <div
              className={cn("transition-opacity", (list.loading || Boolean(list.error)) && data && "opacity-60")}
              aria-busy={list.loading || undefined}
            >
              <AuditTable
                entries={data?.items ?? []}
                selectedId={openId}
                onOpen={(id) => navigate({ id })}
                onFilterRequest={(requestId) => navigate({ filters: { ...filters, request_id: requestId } })}
                pagination={{
                  page: data?.page ?? page,
                  pageSize: data?.page_size ?? AUDIT_PAGE_SIZE,
                  total: data?.total ?? 0,
                  onPageChange: (p) => navigate({ page: p }),
                }}
                empty={
                  !data ? (
                    <p role="status" className="px-8 py-14 text-center text-sm text-muted-foreground">
                      {t("loading")}
                    </p>
                  ) : (
                    <EmptyState
                      variant="inline"
                      title={active > 0 ? t("emptyFiltered") : t("empty")}
                      description={active > 0 ? t("filtersActive", { count: active }) : undefined}
                      action={
                        active > 0 ? (
                          <Button variant="outline" size="sm" onClick={() => navigate({ filters: {} })}>
                            {t("clearFilters")}
                          </Button>
                        ) : undefined
                      }
                    />
                  )
                }
              />
            </div>
          </>
        )}
      </section>

      <AuditDetailSheet entryId={openId} onClose={() => navigate({ id: null })} onOpen={(id) => navigate({ id })} />
    </>
  );
}
