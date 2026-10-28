"use client";

import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";

import { FilterChips, type FilterChip } from "@/components/common/filter-chips";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import type { FieldOut } from "@/lib/api";
import { addFilterValue, filterKind, filterValueProblem, removeFilterValue } from "@/lib/entities";

import { headerText } from "./field-value";

/** The value input for one field, by type: enum / bool pick from options, numbers and dates are typed. */
function ValueControl({
  field,
  value,
  onChange,
  id,
  invalid,
}: {
  field: FieldOut;
  value: string;
  onChange: (value: string) => void;
  id: string;
  invalid: boolean;
}) {
  const t = useTranslations("data.filters");
  const kind = filterKind(field);
  const label = t("valueFor", { field: field.label });
  if (kind === "enum" || kind === "bool") {
    const options = kind === "enum" ? field.enum_values : ["true", "false"];
    return (
      <NativeSelect id={id} aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className="w-44">
        <option value="">{t("pickValue")}</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {kind === "bool" ? (option === "true" ? t("yes") : t("no")) : option}
          </option>
        ))}
      </NativeSelect>
    );
  }
  return (
    <Input
      id={id}
      aria-label={label}
      aria-invalid={invalid || undefined}
      type={kind === "number" ? "number" : kind === "date" ? "date" : "text"}
      step={field.type === "int" ? 1 : "any"}
      inputMode={kind === "number" ? "decimal" : undefined}
      value={value}
      placeholder={kind === "text" ? t("exactPlaceholder") : undefined}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 w-44 bg-background"
    />
  );
}

/**
 * Filter builder (DESIGN 4.11) for any schema: pick a field, give a value in a control that
 * fits its type, add it. The API matches values exactly; several values of one field match any
 * of them. Active filters show as chips.
 */
export function RecordFilters({
  fields,
  filters,
  onChange,
}: {
  fields: readonly FieldOut[];
  filters: Readonly<Record<string, readonly string[]>>;
  onChange: (filters: Record<string, string[]>) => void;
}) {
  const t = useTranslations("data.filters");
  const baseId = useId();
  const [fieldId, setFieldId] = useState<string>("");
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const field = fields.find((f) => f.id === fieldId) ?? fields[0];

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!field) return;
    const issue = filterValueProblem(field, value);
    if (issue) {
      setProblem(t(`problem.${issue}`));
      return;
    }
    setProblem(null);
    onChange(addFilterValue(filters, field.id, value.trim()));
    setValue("");
  };

  const labelOf = (id: string) => fields.find((f) => f.id === id);
  const chips: FilterChip[] = Object.entries(filters).flatMap(([id, values]) => {
    const spec = labelOf(id);
    return values.map((v) => ({
      id: `${id}=${v}`,
      label: spec ? spec.label : id,
      value: spec?.type === "bool" ? (v === "true" ? t("yes") : t("no")) : v,
      onRemove: () => onChange(removeFilterValue(filters, id, v)),
    }));
  });
  const anyOf = Object.values(filters).some((values) => values.length > 1);

  if (fields.length === 0) return null;
  return (
    <div className="mb-3 rounded-xl bg-card p-3 shadow-sm ring-1 ring-foreground/10">
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2" aria-label={t("label")}>
        <NativeSelect
          id={`${baseId}-field`}
          aria-label={t("field")}
          value={field?.id ?? ""}
          onChange={(e) => {
            setFieldId(e.target.value);
            setValue("");
            setProblem(null);
          }}
          className="w-48"
        >
          {fields.map((f) => (
            <option key={f.id} value={f.id}>
              {headerText(f)}
            </option>
          ))}
        </NativeSelect>
        <span aria-hidden="true" className="text-sm text-faint">
          =
        </span>
        {field ? (
          <ValueControl
            key={field.id}
            id={`${baseId}-value`}
            field={field}
            value={value}
            onChange={(v) => {
              setValue(v);
              setProblem(null);
            }}
            invalid={problem !== null}
          />
        ) : null}
        <Button type="submit" variant="outline" size="sm" className="h-8">
          <Plus aria-hidden="true" />
          {t("add")}
        </Button>
        <p className="text-caption text-faint">{anyOf ? t("anyOfHint") : t("exactHint")}</p>
      </form>
      {problem ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {problem}
        </p>
      ) : null}
      <FilterChips chips={chips} onClearAll={() => onChange({})} />
    </div>
  );
}
