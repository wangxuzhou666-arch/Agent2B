"use client";

import { Database } from "lucide-react";
import type { Route } from "next";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useMemo, useState } from "react";

import type { SortState } from "@/components/common/data-table";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState, useErrorMessage } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiResource } from "@/hooks/use-api-resource";
import { api } from "@/lib/api";
import { RECORDS_PAGE_SIZE } from "@/lib/config";
import {
  dataSearch,
  filtersFor,
  locateView,
  readDataLocation,
  sortParam,
  viewFields,
  type DataLocation,
} from "@/lib/entities";
import { cn } from "@/lib/utils";

import { EntityPicker } from "./entity-picker";
import { RecordDetailSheet } from "./record-detail";
import { RecordFilters } from "./record-filters";
import { RecordTable, type RecordRow } from "./record-table";

const ALL = "__all__";

function TableSkeleton() {
  const t = useTranslations("common");
  return (
    <div role="status" aria-busy="true" className="space-y-2 rounded-xl bg-card p-4 shadow-sm ring-1 ring-foreground/10">
      <span className="sr-only">{t("loading")}</span>
      <Skeleton className="h-8 w-full" />
      {[0, 1, 2, 3, 4].map((i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/**
 * The Data window: every entity the role may see, its categories as tabs, and a schema-driven
 * table with server-side sort, equality filters and paging. Nothing here names an entity or a
 * field; a drop-in category appears as a new tab with its attrs as columns.
 */
export function DataView() {
  const t = useTranslations("data");
  const pages = useTranslations("pages.data");
  const describeError = useErrorMessage();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const location = useMemo(() => readDataLocation(new URLSearchParams(params.toString())), [params]);
  const [openRow, setOpenRow] = useState<RecordRow | null>(null);

  // labels and descriptions are the server's, in the console's language: fetched again on a switch
  const locale = useLocale();
  const entities = useApiResource(`entities:${locale}`, (signal) => api.listEntities(signal));
  const list = entities.data?.items ?? null;
  const view = useMemo(
    () => (list ? locateView(list, location.entity, location.category) : null),
    [list, location.entity, location.category],
  );
  const fields = useMemo(() => (view ? viewFields(view) : []), [view]);
  const sortable = view && location.sort && fields.some((f) => f.id === location.sort?.columnId) ? location.sort : null;
  const filters = useMemo(() => filtersFor(location.filters, fields), [location.filters, fields]);

  const recordsKey = view ? JSON.stringify([view.entityId, sortParam(sortable), location.page, filters]) : null;
  const records = useApiResource(recordsKey, (signal) =>
    api.listRecords(
      view?.entityId ?? "",
      { sort: sortParam(sortable), page: location.page, page_size: RECORDS_PAGE_SIZE },
      filters,
      signal,
    ),
  );
  // rows of another entity never render under this one's columns, and rows of an earlier
  // query never render under the current filters once its replacement failed
  const page =
    records.data && view && records.data.entity === view.entityId && !records.error ? records.data : null;

  const navigate = useCallback(
    (next: Partial<DataLocation>) => {
      const merged: DataLocation = { ...location, ...next };
      router.replace(`${pathname}${dataSearch(merged)}` as Route, { scroll: false });
    },
    [location, pathname, router],
  );

  const onOpen = useCallback((row: RecordRow) => setOpenRow(row), []);

  if (entities.error && !list) {
    return (
      <>
        <PageHeader title={pages("title")} description={pages("description")} />
        <ErrorState error={entities.error} onRetry={entities.reload} />
      </>
    );
  }
  if (!list || !view) {
    return (
      <>
        <PageHeader title={pages("title")} description={pages("description")} />
        {list && list.length === 0 ? (
          <EmptyState icon={Database} title={t("noEntitiesTitle")} description={t("noEntitiesBody")} />
        ) : (
          <TableSkeleton />
        )}
      </>
    );
  }

  const activeFilters = Object.values(filters).reduce((n, values) => n + values.length, 0);
  const tabValue = view.category?.id ?? ALL;

  const table = (
    <>
      <RecordFilters
        key={view.entityId}
        fields={fields}
        filters={filters}
        onChange={(next) => navigate({ filters: next, page: 1 })}
      />
      {records.error ? (
        <div role="alert" className="mb-3 flex flex-wrap items-center gap-3 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive ring-1 ring-destructive/30">
          <span className="min-w-0 flex-1">{describeError(records.error)}</span>
          {activeFilters > 0 ? (
            <Button variant="outline" size="xs" onClick={() => navigate({ filters: {}, page: 1 })}>
              {t("clearFilters")}
            </Button>
          ) : (
            <Button variant="outline" size="xs" onClick={records.reload}>
              {t("retry")}
            </Button>
          )}
        </div>
      ) : null}
      {records.error ? null : !page && records.loading ? (
        <TableSkeleton />
      ) : (
        <div className={cn("transition-opacity", records.loading && "opacity-60")} aria-busy={records.loading || undefined}>
          <RecordTable
            view={view}
            rows={page?.items ?? []}
            sort={sortable as SortState | null}
            onSortChange={(sort) => navigate({ sort, page: 1 })}
            onOpen={onOpen}
            pagination={{
              page: page?.page ?? location.page,
              pageSize: page?.page_size ?? RECORDS_PAGE_SIZE,
              total: page?.total ?? 0,
              onPageChange: (p) => navigate({ page: p }),
            }}
            header={
              <>
                <span className="text-caption font-medium text-foreground">{view.label}</span>
                <span className="font-mono text-caption text-faint">{view.entityId}</span>
                {activeFilters > 0 ? (
                  <span className="text-caption text-faint">{t("filteredBy", { count: activeFilters })}</span>
                ) : null}
              </>
            }
            empty={
              <EmptyState
                variant="inline"
                title={activeFilters > 0 ? t("emptyFiltered") : t("empty")}
                action={
                  activeFilters > 0 ? (
                    <Button variant="outline" size="sm" onClick={() => navigate({ filters: {}, page: 1 })}>
                      {t("clearFilters")}
                    </Button>
                  ) : undefined
                }
              />
            }
          />
        </div>
      )}
    </>
  );

  return (
    <>
      <PageHeader title={pages("title")} description={pages("description")} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <EntityPicker
          entities={list}
          selected={view.root.id}
          onSelect={(id) => navigate({ entity: id, category: null, sort: null, page: 1, filters: {} })}
        />
        <div className="min-w-0">
          <div className="mb-3">
            <h2 className="text-base font-semibold text-foreground">{view.root.label}</h2>
            {view.root.description ? (
              <p className="mt-0.5 text-sm text-muted-foreground">{view.root.description}</p>
            ) : null}
          </div>
          {view.root.categories.length > 0 ? (
            <Tabs
              value={tabValue}
              onValueChange={(value) =>
                navigate({ entity: view.root.id, category: value === ALL ? null : value, page: 1, filters: {} })
              }
            >
              <TabsList variant="line" aria-label={t("categories", { entity: view.root.label })}>
                <TabsTrigger value={ALL}>{t("allCategories")}</TabsTrigger>
                {view.root.categories.map((category) => (
                  <TabsTrigger key={category.id} value={category.id}>
                    {category.label}
                  </TabsTrigger>
                ))}
              </TabsList>
              <TabsContent value={tabValue}>
                {view.category?.description ? (
                  <p className="mb-3 text-xs text-faint">{view.category.description}</p>
                ) : null}
                {table}
              </TabsContent>
            </Tabs>
          ) : (
            table
          )}
        </div>
      </div>
      <RecordDetailSheet view={view} row={openRow} onClose={() => setOpenRow(null)} />
    </>
  );
}
