"use client";

import { useTranslations } from "next-intl";

import { ErrorState } from "@/components/common/error-state";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useApiResource } from "@/hooks/use-api-resource";
import { api, type FieldOut } from "@/lib/api";
import { categoryOf, ENTITY_KEY, familyLabel, unitSuffix, type EntityView } from "@/lib/entities";
import { cn } from "@/lib/utils";

import { FieldValue, RestrictedMark } from "./field-value";
import type { RecordRow } from "./record-table";

function FieldList({ fields, record }: { fields: readonly FieldOut[]; record: RecordRow }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,10rem)_1fr] items-baseline gap-x-4 gap-y-1.5">
      {fields.map((field) => {
        const unit = unitSuffix(field);
        const value = record[field.id];
        return (
          <div key={field.id} className="contents">
            <dt className="flex items-center gap-1 text-caption text-muted-foreground">
              {field.label}
              {field.sensitivity === "restricted" ? <RestrictedMark /> : null}
            </dt>
            <dd className={cn("min-w-0 text-sm break-words text-foreground", (field.type === "int" || field.type === "float") && "tabular-nums")}>
              <FieldValue field={field} value={value} />
              {unit && value !== null && value !== undefined && value !== "" ? (
                <span className="ml-1 text-xs text-faint">{unit}</span>
              ) : null}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/**
 * One record (detail sheet): the core fields every family member shares, then the attributes
 * of the row's own category, both straight from the spec. The row from the listing shows at
 * once; the record endpoint then refreshes it.
 */
export function RecordDetailSheet({
  view,
  row,
  onClose,
}: {
  view: EntityView;
  row: RecordRow | null;
  onClose: () => void;
}) {
  const t = useTranslations("data.detail");
  const common = useTranslations("common");
  const root = view.root;
  const pk = row ? row[root.primary_key] : undefined;
  const rowEntity = row && typeof row[ENTITY_KEY] === "string" ? (row[ENTITY_KEY] as string) : view.entityId;
  const fresh = useApiResource(
    row && pk !== undefined && pk !== null ? `record:${rowEntity}:${String(pk)}` : null,
    (signal) => api.getRecord(rowEntity, String(pk), signal),
  );
  const record: RecordRow | null = row ? { ...row, ...(fresh.data && !fresh.stale ? fresh.data : {}) } : null;
  const category = record ? categoryOf(root, record) : null;
  const attrs = category ? category.attrs : root.attrs;
  const labelValue = record ? record[root.label_field] : undefined;
  const title =
    labelValue !== null && labelValue !== undefined && labelValue !== "" && root.label_field !== root.primary_key
      ? String(labelValue)
      : String(pk ?? "");
  const family = record ? familyLabel(root, record[ENTITY_KEY]) ?? view.label : view.label;

  return (
    <Sheet open={row !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent side="right" closeLabel={common("close")} className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b border-border pr-12">
          <SheetTitle className="font-serif text-lg font-semibold tracking-tight break-words">{title}</SheetTitle>
          <SheetDescription className="text-xs text-faint">
            {family} · <span className="font-mono">{String(pk ?? "")}</span>
          </SheetDescription>
        </SheetHeader>
        {record ? (
          <div className="flex-1 space-y-6 overflow-y-auto p-4">
            {fresh.error ? (
              <ErrorState variant="inline" error={fresh.error} onRetry={fresh.reload} className="px-0 py-2 text-left" />
            ) : null}
            <section aria-labelledby="record-fields" className="space-y-2">
              <h3 id="record-fields" className="text-caption font-medium tracking-wide text-muted-foreground">
                {t("coreFields", { entity: root.label })}
              </h3>
              <FieldList fields={root.fields} record={record} />
            </section>
            {attrs.length > 0 ? (
              <section aria-labelledby="record-attrs" className="space-y-2">
                <h3 id="record-attrs" className="text-caption font-medium tracking-wide text-muted-foreground">
                  {category ? t("categoryAttrs", { category: category.label }) : t("attrs")}
                </h3>
                <FieldList fields={attrs} record={record} />
              </section>
            ) : null}
            <p className="text-xs text-faint">{t("projected")}</p>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
