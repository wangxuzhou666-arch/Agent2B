"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { DataTable, type DataTableColumn, type DataTablePagination } from "@/components/common/data-table";
import { RouteSourceBadge } from "@/components/common/route-source-badge";
import { StatusPill } from "@/components/common/status-label";
import type { AuditEntryOut } from "@/lib/api";
import { shortId } from "@/lib/audit";
import { formatTimestamp } from "@/lib/format";

const none = <span className="text-faint">—</span>;

/**
 * The audit log (DESIGN 10.7): newest first, read-only. The time opens the entry's replay;
 * the request id narrows the log to every record of that request.
 */
export function AuditTable({
  entries,
  pagination,
  onOpen,
  onFilterRequest,
  selectedId,
  empty,
}: {
  entries: readonly AuditEntryOut[];
  pagination: DataTablePagination;
  onOpen: (id: number) => void;
  onFilterRequest: (requestId: string) => void;
  selectedId: number | null;
  empty?: React.ReactNode;
}) {
  const t = useTranslations("audit.table");
  const columns = useMemo<DataTableColumn<AuditEntryOut>[]>(
    () => [
      {
        id: "ts",
        header: t("time"),
        sortable: false,
        value: (e) => e.ts,
        cell: (e) => {
          const time = formatTimestamp(e.ts) ?? e.ts;
          return (
            <button
              type="button"
              onClick={() => onOpen(e.id)}
              // the name starts with the visible time (WCAG 2.5.3), then says what the button does
              aria-label={t("open", { time, id: e.id })}
              aria-current={selectedId === e.id ? "true" : undefined}
              className="rounded-sm text-left text-muted-foreground tabular-nums underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {time}
            </button>
          );
        },
      },
      {
        id: "user",
        header: t("user"),
        sortable: false,
        value: (e) => e.user_id,
        cell: (e) => <span className="font-medium text-foreground">{e.user_id}</span>,
      },
      {
        id: "target",
        header: t("target"),
        sortable: false,
        value: (e) => e.target,
        cell: (e) =>
          e.target ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="font-mono text-xs text-foreground">{e.target}</span>
              {e.effect === "write" ? (
                <span className="rounded-sm bg-warning/10 px-1 text-xs text-warning dark:bg-warning/20">{t("write")}</span>
              ) : null}
            </span>
          ) : (
            none
          ),
      },
      {
        id: "route",
        header: t("route"),
        sortable: false,
        value: (e) => e.route_source,
        cell: (e) => (e.route_source ? <RouteSourceBadge source={e.route_source} /> : none),
      },
      {
        id: "status",
        header: t("status"),
        sortable: false,
        value: (e) => e.status,
        cell: (e) => <StatusPill status={e.status} />,
      },
      {
        id: "rows",
        header: t("rows"),
        sortable: false,
        numeric: true,
        value: (e) => e.row_count,
      },
      {
        id: "request",
        header: t("request"),
        sortable: false,
        value: (e) => e.request_id,
        cell: (e) =>
          e.request_id ? (
            <button
              type="button"
              title={e.request_id}
              onClick={() => onFilterRequest(e.request_id as string)}
              aria-label={t("filterRequest", { id: e.request_id })}
              className="rounded-sm font-mono text-xs text-faint outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {shortId(e.request_id)}
            </button>
          ) : (
            none
          ),
      },
      {
        id: "id",
        header: t("id"),
        sortable: false,
        numeric: true,
        value: (e) => e.id,
        cell: (e) => <span className="font-mono text-xs text-faint">#{e.id}</span>,
      },
    ],
    [t, onOpen, onFilterRequest, selectedId],
  );

  return (
    <DataTable
      columns={columns}
      rows={entries}
      rowKey={(e) => String(e.id)}
      sort={null}
      pagination={pagination}
      caption={t("caption")}
      empty={empty}
    />
  );
}
