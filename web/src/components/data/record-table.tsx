"use client";

import { useTranslations } from "next-intl";
import { useMemo, type ReactNode } from "react";

import { DataTable, type DataTableColumn, type DataTablePagination, type SortState } from "@/components/common/data-table";
import { Badge } from "@/components/ui/badge";
import { ENTITY_KEY, familyLabel, isNumericField, viewFields, type EntityView } from "@/lib/entities";

import { FieldHeader, FieldValue, headerText } from "./field-value";

export type RecordRow = Record<string, unknown>;

/**
 * Columns straight from the spec: core fields, then the view's attrs; numbers right-aligned
 * with tabular figures and the unit in the header; the primary key opens the record. On a
 * root that has categories, a "Category" column says which family member each row is.
 */
export function recordColumns(
  view: EntityView,
  options: { categoryHeader: string; openLabel: (pk: string) => string; onOpen: (row: RecordRow) => void },
): DataTableColumn<RecordRow>[] {
  const pk = view.root.primary_key;
  const columns: DataTableColumn<RecordRow>[] = viewFields(view).map((field) => ({
    id: field.id,
    header: <FieldHeader field={field} />,
    headerLabel: headerText(field),
    value: (row: RecordRow) => row[field.id],
    numeric: isNumericField(field),
    className: field.type === "date" ? "tabular-nums" : undefined,
    cell:
      field.id === pk
        ? (row: RecordRow) => {
            const key = row[pk];
            if (key === null || key === undefined || key === "") return <FieldValue field={field} value={key} />;
            return (
              <button
                type="button"
                onClick={() => options.onOpen(row)}
                aria-label={options.openLabel(String(key))}
                className="rounded-sm font-medium text-foreground underline-offset-2 outline-none hover:text-primary hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {String(key)}
              </button>
            );
          }
        : (row: RecordRow) => <FieldValue field={field} value={row[field.id]} />,
  }));
  if (!view.category && view.root.categories.length > 0) {
    const family: DataTableColumn<RecordRow> = {
      id: ENTITY_KEY,
      header: options.categoryHeader,
      headerLabel: options.categoryHeader,
      sortable: false,
      value: (row) => row[ENTITY_KEY],
      cell: (row) => {
        const label = familyLabel(view.root, row[ENTITY_KEY]);
        return label ? (
          <Badge variant="outline" className="font-normal">
            {label}
          </Badge>
        ) : (
          <span className="text-faint">—</span>
        );
      },
    };
    // right after the key and label columns, where it reads as part of the identity
    const at = Math.min(columns.length, 2);
    columns.splice(at, 0, family);
  }
  return columns;
}

export function RecordTable({
  view,
  rows,
  sort,
  onSortChange,
  pagination,
  onOpen,
  header,
  footer,
  empty,
}: {
  view: EntityView;
  rows: readonly RecordRow[];
  sort: SortState | null;
  onSortChange: (sort: SortState | null) => void;
  pagination: DataTablePagination;
  onOpen: (row: RecordRow) => void;
  header?: ReactNode;
  footer?: ReactNode;
  empty?: ReactNode;
}) {
  const t = useTranslations("data");
  const columns = useMemo(
    () =>
      recordColumns(view, {
        categoryHeader: t("categoryColumn"),
        openLabel: (pk) => t("openRecord", { id: pk }),
        onOpen,
      }),
    [view, t, onOpen],
  );
  const pk = view.root.primary_key;
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(row, index) => (row[pk] === undefined || row[pk] === null ? `row-${index}` : String(row[pk]))}
      sort={sort}
      onSortChange={onSortChange}
      pagination={pagination}
      // the header stays in view while a page of records scrolls under it
      maxHeight="max(24rem, calc(100svh - 18rem))"
      caption={t("tableCaption", { entity: view.label })}
      header={header}
      footer={footer}
      empty={empty}
    />
  );
}
