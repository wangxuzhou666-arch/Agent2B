/** Synthetic specs and records shared by the P4b tests (no real names, no real data). */
import type { AuditDetailOut, AuditEntryOut, EntityOut, FieldOut } from "@/lib/api";

export const field = (over: Partial<FieldOut> & Pick<FieldOut, "id" | "label">): FieldOut => ({
  description: "",
  type: "str",
  required: false,
  aliases: [],
  enum_values: [],
  unit: null,
  sensitivity: "internal",
  ...over,
});

/** A root with two categories; the second is a "drop-in" the frontend has never heard of. */
export const ITEM: EntityOut = {
  id: "item",
  label: "Item",
  description: "A stocked item.",
  primary_key: "item_id",
  label_field: "name",
  fields: [
    field({ id: "item_id", label: "Item ID", required: true }),
    field({ id: "name", label: "Name", required: true, sensitivity: "public" }),
    field({ id: "unit_cost", label: "Unit Cost", type: "float" }),
    field({ id: "daily_demand", label: "Daily Demand", type: "float", unit: "units/day" }),
    field({ id: "lead_time_days", label: "Lead Time (days)", type: "int", unit: "days" }),
    field({ id: "grade", label: "Grade", type: "enum", enum_values: ["A", "B", "C"] }),
    field({ id: "margin_pct", label: "Margin %", type: "float", unit: "%", sensitivity: "restricted" }),
  ],
  attrs: [],
  categories: [
    {
      id: "part",
      label: "Part",
      description: "A maintenance part.",
      extends: "item",
      attrs: [field({ id: "machine", label: "Machine" })],
    },
    {
      id: "chilled",
      label: "Chilled Item",
      description: "Kept cold.",
      extends: "item",
      attrs: [
        field({ id: "storage_temp", label: "Storage Temp", type: "float", unit: "C" }),
        field({ id: "fragile", label: "Fragile", type: "bool" }),
      ],
    },
  ],
  hidden_fields: 0,
};

export const SITE: EntityOut = {
  id: "site",
  label: "Site",
  description: "A place that holds stock.",
  primary_key: "site_id",
  label_field: "site_id",
  fields: [field({ id: "site_id", label: "Site ID", required: true }), field({ id: "opened", label: "Opened", type: "date" })],
  attrs: [],
  categories: [],
  hidden_fields: 0,
};

export const ENTITIES: EntityOut[] = [ITEM, SITE];

export const auditEntry = (over: Partial<AuditEntryOut> & Pick<AuditEntryOut, "id">): AuditEntryOut => ({
  ts: "2026-10-26T08:00:00Z",
  user_id: "demo-operator",
  roles: ["operator"],
  target: "make_order",
  effect: "write",
  status: "ok",
  route_source: "rule",
  params_digest: null,
  params: {},
  row_count: null,
  grant_id: null,
  problem: null,
  request_id: null,
  parent_id: null,
  duration_ms: null,
  ...over,
});

export const auditDetail = (over: Partial<AuditDetailOut> & Pick<AuditDetailOut, "id">): AuditDetailOut => ({
  ...auditEntry(over),
  trace: [],
  ...over,
});
