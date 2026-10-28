"use client";

import { ArrowRight, LoaderCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { Fragment, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import type { FieldOut, ImportPreviewOut, PreviewRow } from "@/lib/api";
import type { ImportTarget } from "@/lib/entities";
import { cellErrors, type UploadIssue, type UploadedFile } from "@/lib/imports";
import { cn } from "@/lib/utils";

import { UploadIssueNotice } from "./upload-issue";

/** Rows drawn in the preview table; every row is validated either way. */
export const PREVIEW_ROW_LIMIT = 300;

const strong = (chunks: React.ReactNode) => <span className="font-semibold text-foreground tabular-nums">{chunks}</span>;

/**
 * File header → field, from the server's mapping, as a compact grid; headers that map to no
 * field are flagged (they are not imported).
 */
function MappingList({ preview, fieldsById }: { preview: ImportPreviewOut; fieldsById: Map<string, FieldOut> }) {
  const t = useTranslations("import.preview");
  const item = "flex min-w-0 items-baseline gap-1.5 rounded-md px-2.5 py-1.5 text-xs";
  return (
    <ul aria-label={t("mappingTitle")} className="grid gap-1 rounded-lg bg-muted/40 p-1.5 md:grid-cols-2">
      {preview.mapping.map((m) => {
        const field = fieldsById.get(m.field_id);
        return (
          <li key={`${m.column}-${m.header}`} className={item}>
            <span className="min-w-0 truncate text-foreground" title={m.header}>
              {m.header}
            </span>
            <ArrowRight aria-hidden="true" className="size-3 shrink-0 self-center text-faint" />
            <span className="sr-only">{t("mapsTo")}</span>
            <span className="min-w-0 truncate">
              <span className="font-medium text-foreground">{field?.label ?? m.field_id}</span>{" "}
              <span className="font-mono text-faint">{m.field_id}</span>
              {!field ? <span className="text-faint"> · {t("hiddenField")}</span> : null}
            </span>
          </li>
        );
      })}
      {preview.unmapped.map((header) => (
        <li key={`unmapped-${header}`} className={item} data-unmapped>
          <span className="min-w-0 truncate text-foreground" title={header}>
            {header}
          </span>
          <ArrowRight aria-hidden="true" className="size-3 shrink-0 self-center text-faint" />
          <span className="rounded-full bg-warning/10 px-2 font-medium text-warning dark:bg-warning/20">{t("unmapped")}</span>
        </li>
      ))}
    </ul>
  );
}

function RowStatus({ row }: { row: PreviewRow }) {
  const t = useTranslations("import.preview");
  if (row.errors.length > 0) {
    return (
      <span className="inline-flex h-5 items-center rounded-4xl border border-destructive/40 px-2 text-xs font-medium whitespace-nowrap text-destructive">
        {t("statusError")}
      </span>
    );
  }
  const label = row.action === "update" ? t("statusUpdate") : row.action === "new" ? t("statusNew") : t("statusValid");
  return (
    <span className="inline-flex h-5 items-center rounded-4xl border border-border px-2 text-xs font-medium whitespace-nowrap text-muted-foreground">
      {label}
    </span>
  );
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/**
 * The validated rows (DESIGN 10.9): Row, Status, then the mapped columns. A row with errors is
 * tinted; the cells at fault are marked (`aria-invalid`) and its messages follow in a sub-row.
 */
export function PreviewTable({
  preview,
  fields,
  errorsOnly,
}: {
  preview: ImportPreviewOut;
  fields: readonly FieldOut[];
  errorsOnly: boolean;
}) {
  const t = useTranslations("import.preview");
  const fieldsById = useMemo(() => new Map(fields.map((f) => [f.id, f])), [fields]);
  // the mapping carries field ids; labels come from the spec (a field hidden for the role keeps its id)
  const columns = preview.mapping.map((m) => {
    const field = fieldsById.get(m.field_id);
    return { id: m.field_id, label: field?.label ?? m.field_id, numeric: field?.type === "int" || field?.type === "float" };
  });
  const labelled = columns.map((c) => ({ id: c.id, label: c.label }));
  const rows = (errorsOnly ? preview.rows.filter((r) => r.errors.length > 0) : preview.rows).slice(0, PREVIEW_ROW_LIMIT);

  return (
    <div className="max-h-80 overflow-auto rounded-lg ring-1 ring-border" role="region" aria-label={t("rowsTitle")} tabIndex={0}>
      <table className="w-full border-separate border-spacing-0 text-xs">
        <caption className="sr-only">{t("rowsTitle")}</caption>
        <thead>
          <tr>
            {[
              { label: t("row"), numeric: true },
              { label: t("status"), numeric: false },
              ...columns,
            ].map((column, i) => (
              <th
                key={`${i}-${column.label}`}
                scope="col"
                className={cn(
                  "sticky top-0 z-[1] bg-card px-3 py-2 text-left font-medium whitespace-nowrap text-muted-foreground shadow-[inset_0_-1px_0_var(--border)]",
                  column.numeric && "text-right",
                )}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const problems = cellErrors(row, labelled);
            const failed = row.errors.length > 0;
            const reasonId = `import-row-${row.row}-reasons`;
            return (
              <Fragment key={row.row}>
                <tr className={cn(failed && "bg-destructive/10")} data-row={row.row} data-invalid={failed || undefined}>
                  <td className="border-b border-border/60 px-3 py-1.5 text-right whitespace-nowrap text-muted-foreground tabular-nums">{row.row}</td>
                  <td className="border-b border-border/60 px-3 py-1.5 whitespace-nowrap">
                    <RowStatus row={row} />
                  </td>
                  {columns.map((column) => {
                    const messages = problems.byField[column.id];
                    const bad = Boolean(messages?.length);
                    return (
                      <td
                        key={column.id}
                        aria-invalid={bad || undefined}
                        aria-describedby={bad ? reasonId : undefined}
                        title={bad ? messages?.join("; ") : undefined}
                        className={cn(
                          "border-b border-border/60 px-3 py-1.5 whitespace-nowrap",
                          column.numeric && "text-right tabular-nums",
                          bad ? "bg-destructive/15 font-medium text-destructive dark:bg-destructive/25" : "text-muted-foreground",
                        )}
                      >
                        {cellText(row.values[column.id]) || <span className={bad ? undefined : "text-faint"}>—</span>}
                      </td>
                    );
                  })}
                </tr>
                {failed ? (
                  <tr className="bg-destructive/10">
                    <td
                      id={reasonId}
                      colSpan={columns.length + 2}
                      className="border-b border-border/60 px-3 py-1.5 text-xs text-destructive"
                    >
                      {row.errors.join("; ")}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Step 2: what the server made of the file. Mapping, counts, every row with its problems;
 * nothing is written yet. Import asks for the one-time confirmation.
 */
export function PreviewStep({
  target,
  file,
  preview,
  committing,
  issue,
  onCommit,
  onCancel,
}: {
  target: ImportTarget | null;
  file: UploadedFile;
  preview: ImportPreviewOut;
  committing: boolean;
  issue: UploadIssue | null;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("import.preview");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const fields = useMemo(() => (target ? [...target.view.fields, ...target.view.attrs] : []), [target]);
  const fieldsById = useMemo(() => new Map(fields.map((f) => [f.id, f])), [fields]);
  const total = preview.rows.length;
  const shown = errorsOnly ? preview.error_count : total;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-medium break-all text-foreground">{file.name}</span>
        <span className="text-xs text-faint uppercase">{preview.file_type}</span>
        {preview.encoding ? <span className="text-xs text-faint">{preview.encoding}</span> : null}
        <ArrowRight aria-hidden="true" className="size-3.5 text-faint" />
        <span className="text-foreground">{target?.path ?? preview.entity}</span>
      </div>

      <p className="text-xs text-muted-foreground">
        {t.rich("stats", {
          rows: total,
          valid: preview.valid_count,
          r: strong,
          v: strong,
        })}
        {preview.error_count > 0 ? (
          <span className="text-destructive"> · {t("errors", { count: preview.error_count })}</span>
        ) : null}
      </p>

      <section aria-labelledby="import-mapping" className="space-y-2">
        <h3 id="import-mapping" className="text-sm font-medium text-foreground">
          {t("mappingTitle")}
        </h3>
        <MappingList preview={preview} fieldsById={fieldsById} />
        {preview.unmapped.length > 0 ? <p className="text-xs text-warning">{t("unmappedNote", { count: preview.unmapped.length })}</p> : null}
      </section>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 id="import-rows" className="text-sm font-medium text-foreground">
            {t("rowsTitle")}
          </h3>
          {preview.error_count > 0 ? (
            <Button variant="ghost" size="xs" aria-pressed={errorsOnly} onClick={() => setErrorsOnly((v) => !v)}>
              {errorsOnly ? t("showAll") : t("showErrors", { count: preview.error_count })}
            </Button>
          ) : null}
        </div>
        <PreviewTable preview={preview} fields={fields} errorsOnly={errorsOnly} />
        {shown > PREVIEW_ROW_LIMIT ? (
          <p className="text-xs text-faint">{t("truncated", { shown: PREVIEW_ROW_LIMIT, total: shown })}</p>
        ) : null}
      </div>

      {issue ? <UploadIssueNotice issue={issue} /> : null}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button variant="ink" disabled={committing || preview.valid_count === 0} onClick={onCommit} className="h-9 px-3.5">
            {committing ? <LoaderCircle aria-hidden="true" className="animate-spin" /> : null}
            {t("commit", { count: preview.valid_count })}
          </Button>
          <Button variant="outline" disabled={committing} onClick={onCancel} className="h-9 px-3.5 text-muted-foreground">
            {t("cancel")}
          </Button>
        </div>
        <p className="text-xs text-faint">{preview.valid_count === 0 ? t("nothingValid") : t("footnote")}</p>
      </div>
    </div>
  );
}
