"use client";

import { useMemo } from "react";

import type { Provenance } from "@/lib/api";
import { humanizeKey } from "@/lib/format";

import { DataTable, type DataTableColumn } from "./data-table";
import { ProvenanceLine, useProvenanceSource } from "./provenance-line";

type Row = Record<string, unknown>;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

const present = (v: unknown) => v !== null && v !== undefined && v !== "";

/**
 * Column types come from the values the tool returned: all-numeric columns are right-aligned
 * with tabular figures, dates get tabular figures, everything else reads left to right.
 */
export function inferColumns(columns: readonly string[], rows: readonly Row[]): DataTableColumn<Row>[] {
  return columns.map((name) => {
    const values = rows.map((row) => row[name]).filter(present);
    const numeric = values.length > 0 && values.every((v) => typeof v === "number");
    const dateLike = !numeric && values.length > 0 && values.every((v) => typeof v === "string" && ISO_DATE.test(v));
    return {
      id: name,
      header: humanizeKey(name),
      headerLabel: name,
      value: (row: Row) => row[name],
      numeric,
      className: dateLike ? "tabular-nums" : undefined,
    };
  });
}

/** The rows a tool computed, exactly as the API projected them for this user's role. */
export function ResultTable({
  columns,
  rows,
  provenance,
}: {
  columns: readonly string[];
  rows: readonly Row[];
  provenance: Provenance | null | undefined;
}) {
  const source = useProvenanceSource();
  const tableColumns = useMemo(() => inferColumns(columns, rows), [columns, rows]);
  return (
    <DataTable
      variant="chat"
      columns={tableColumns}
      rows={rows}
      maxHeight="24rem"
      caption={provenance ? source(provenance) : undefined}
      header={provenance ? <ProvenanceLine provenance={provenance} variant="strip" /> : undefined}
    />
  );
}
