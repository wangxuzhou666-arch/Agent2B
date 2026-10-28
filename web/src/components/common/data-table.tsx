"use client";

import { ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown, ChevronUp } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { Fragment, useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { htmlLang } from "@/i18n/config";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import { EmptyState } from "./empty-state";

export type SortDirection = "asc" | "desc";

export interface SortState {
  columnId: string;
  direction: SortDirection;
}

export interface DataTableColumn<Row> {
  id: string;
  header: ReactNode;
  /** Plain-text name for the sort button's label and the header tooltip. */
  headerLabel?: string;
  /** The value used for sorting and, without `cell`, for display. */
  value: (row: Row) => unknown;
  cell?: (row: Row, index: number) => ReactNode;
  /** Right-aligned, tabular figures (DESIGN 3.5). */
  numeric?: boolean;
  /** Default true. */
  sortable?: boolean;
  className?: string;
}

export interface DataTablePagination {
  /** 1-based. */
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

export interface DataTableProps<Row> {
  columns: readonly DataTableColumn<Row>[];
  rows: readonly Row[];
  rowKey?: (row: Row, index: number) => string;
  /** Controlled sort: the caller orders `rows` (e.g. server-side). Omit to sort in the browser. */
  sort?: SortState | null;
  onSortChange?: (sort: SortState | null) => void;
  defaultSort?: SortState | null;
  /** Server-side paging: `rows` is the current page. */
  pagination?: DataTablePagination;
  /** Cap the height (CSS length); the body scrolls under a sticky header. */
  maxHeight?: string;
  /** A full-width row rendered under a row when this returns content (e.g. audit details). */
  rowDetail?: (row: Row, index: number) => ReactNode;
  empty?: ReactNode;
  /** Accessible name for the table (also its scroll region). */
  caption?: string;
  /** Header strip: title / provenance / actions (DESIGN 10.8). */
  header?: ReactNode;
  footer?: ReactNode;
  /** `chat`: bordered, no shadow, inside the transcript. `card`: list card on a page. */
  variant?: "chat" | "card";
  className?: string;
}

function rank(value: unknown): number {
  if (value === null || value === undefined || value === "") return 3;
  if (typeof value === "number" || typeof value === "boolean") return 0;
  return 1;
}

/** Numbers numerically, text with natural collation, empty values always last. */
export function compareValues(a: unknown, b: unknown, collator: Intl.Collator): number {
  const ra = rank(a);
  const rb = rank(b);
  if (ra === 3 || rb === 3) return ra - rb;
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return Number(a) - Number(b);
  const sa = typeof a === "object" ? JSON.stringify(a) : String(a);
  const sb = typeof b === "object" ? JSON.stringify(b) : String(b);
  return collator.compare(sa, sb);
}

export function sortRows<Row>(
  rows: readonly Row[],
  column: DataTableColumn<Row> | undefined,
  direction: SortDirection,
  collator: Intl.Collator,
): Row[] {
  if (!column) return [...rows];
  const sign = direction === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index, value: column.value(row) }))
    .sort((x, y) => {
      const empty = rank(x.value) === 3 || rank(y.value) === 3;
      const order = compareValues(x.value, y.value, collator);
      // empty values stay at the bottom in both directions; ties keep their original order
      return (empty ? order : sign * order) || x.index - y.index;
    })
    .map((entry) => entry.row);
}

function nextSort(current: SortState | null, columnId: string): SortState | null {
  if (current?.columnId !== columnId) return { columnId, direction: "asc" };
  if (current.direction === "asc") return { columnId, direction: "desc" };
  return null;
}

/** Default cell rendering for a value whose type is only known at runtime. */
export function CellValue({ value, locale }: { value: unknown; locale: string }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-faint">—</span>;
  }
  if (typeof value === "number") return <>{formatNumber(value, locale)}</>;
  if (typeof value === "boolean") {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span
          aria-hidden="true"
          className={cn("size-1.5 rounded-full", value ? "bg-success" : "bg-muted-foreground opacity-50")}
        />
        <span className="text-muted-foreground">{String(value)}</span>
      </span>
    );
  }
  if (typeof value === "string") return <>{value}</>;
  return <code className="font-mono text-xs text-muted-foreground">{JSON.stringify(value)}</code>;
}

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  sort: controlledSort,
  onSortChange,
  defaultSort = null,
  pagination,
  maxHeight,
  rowDetail,
  empty,
  caption,
  header,
  footer,
  variant = "card",
  className,
}: DataTableProps<Row>) {
  const t = useTranslations("table");
  const locale = htmlLang[useLocale()];
  const [localSort, setLocalSort] = useState<SortState | null>(defaultSort);
  const controlled = controlledSort !== undefined;
  const sort = controlled ? controlledSort : localSort;

  const collator = useMemo(() => new Intl.Collator(locale, { numeric: true, sensitivity: "base" }), [locale]);
  const shown = useMemo(() => {
    if (controlled || !sort) return rows;
    return sortRows(rows, columns.find((c) => c.id === sort.columnId), sort.direction, collator);
  }, [controlled, sort, rows, columns, collator]);

  const onSort = (columnId: string) => {
    const next = nextSort(sort, columnId);
    if (!controlled) setLocalSort(next);
    onSortChange?.(next);
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl bg-card",
        variant === "chat" ? "border border-border" : "shadow-sm ring-1 ring-foreground/10",
        className,
      )}
    >
      {header ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-1.5">
          {header}
        </div>
      ) : null}
      <div
        className="overflow-auto"
        style={maxHeight ? { maxHeight } : undefined}
        role={caption ? "region" : undefined}
        aria-label={caption}
        tabIndex={maxHeight ? 0 : undefined}
      >
        <table className="w-full caption-bottom border-separate border-spacing-0 text-sm">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead>
            <tr>
              {columns.map((column) => {
                const sortable = column.sortable !== false;
                const active = sort?.columnId === column.id ? sort.direction : null;
                const Icon = active === "asc" ? ChevronUp : active === "desc" ? ChevronDown : ChevronsUpDown;
                return (
                  <th
                    key={column.id}
                    scope="col"
                    title={column.headerLabel}
                    aria-sort={
                      sortable ? (active === "asc" ? "ascending" : active === "desc" ? "descending" : "none") : undefined
                    }
                    className={cn(
                      "sticky top-0 z-[1] h-10 bg-card px-4 py-2.5 text-left align-middle text-caption font-medium tracking-wide whitespace-nowrap text-muted-foreground shadow-[inset_0_-1px_0_var(--border)]",
                      column.numeric && "text-right",
                      column.className,
                    )}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        onClick={() => onSort(column.id)}
                        aria-label={column.headerLabel ? t("sortBy", { column: column.headerLabel }) : undefined}
                        className={cn(
                          "group -mx-1 inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-caption font-medium tracking-wide outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                          column.numeric && "flex-row-reverse",
                          active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        <span>{column.header}</span>
                        <Icon
                          aria-hidden="true"
                          className={cn(
                            "size-3.5",
                            active ? "opacity-70" : "opacity-30 group-hover:opacity-50",
                          )}
                        />
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>
                  {empty ?? <EmptyState variant="inline" title={t("empty")} className="py-10" />}
                </td>
              </tr>
            ) : (
              shown.map((row, index) => {
                const key = rowKey ? rowKey(row, index) : String(index);
                const detail = rowDetail?.(row, index);
                return (
                  <Fragment key={key}>
                    <tr className="transition-colors hover:bg-muted/50 [&:last-child>td]:border-b-0">
                      {columns.map((column) => (
                        <td
                          key={column.id}
                          className={cn(
                            "border-b border-border/60 px-4 py-2.5 align-middle whitespace-nowrap",
                            column.numeric && "text-right tabular-nums",
                            column.className,
                          )}
                        >
                          {column.cell ? (
                            column.cell(row, index)
                          ) : (
                            <CellValue value={column.value(row)} locale={locale} />
                          )}
                        </td>
                      ))}
                    </tr>
                    {detail ? (
                      <tr className="bg-muted/40">
                        <td colSpan={columns.length} className="border-b border-border/60 px-4 py-3">
                          {detail}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {pagination ? <PaginationBar {...pagination} /> : null}
      {footer ? <div className="border-t border-border px-3 py-1.5 text-xs text-faint">{footer}</div> : null}
    </div>
  );
}

function PaginationBar({ page, pageSize, total, onPageChange }: DataTablePagination) {
  const t = useTranslations("table");
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex items-center justify-between border-t border-border px-3 py-2 text-sm text-muted-foreground">
      <span className="tabular-nums">{t("range", { from, to, total })}</span>
      <div className="flex items-center gap-1">
        <span className="mr-2 text-caption tabular-nums text-faint">{t("pageOf", { page, pages })}</span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("previousPage")}
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("nextPage")}
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}
