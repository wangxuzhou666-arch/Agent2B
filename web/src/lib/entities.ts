/**
 * Schema-driven helpers for the Data and Import windows. Everything here is derived from the
 * entity specs `/api/entities` returns for the signed-in role: no entity or field name is
 * written into the frontend, so a drop-in category shows up with zero frontend changes.
 */
import type { CategoryOut, EntityOut, FieldOut, RecordFilters } from "./api";

/** Row key naming the (category) entity a stored record belongs to. */
export const ENTITY_KEY = "_entity";

/** What one tab of the Data window shows: a root entity, or one of its categories. */
export interface EntityView {
  root: EntityOut;
  category: CategoryOut | null;
  /** The id the records and import endpoints take (the category's when one is selected). */
  entityId: string;
  label: string;
  description: string;
  /** Core fields (shared by the whole family). */
  fields: FieldOut[];
  /** Attributes: the root's own, plus the category's when a category is selected. */
  attrs: FieldOut[];
}

function uniqueById(fields: readonly FieldOut[]): FieldOut[] {
  const seen = new Set<string>();
  return fields.filter((field) => (seen.has(field.id) ? false : (seen.add(field.id), true)));
}

/** The root and (optional) category the caller picked, falling back to the first root. */
export function resolveView(
  entities: readonly EntityOut[],
  rootId?: string | null,
  categoryId?: string | null,
): EntityView | null {
  const root = entities.find((e) => e.id === rootId) ?? entities[0];
  if (!root) return null;
  const category = root.categories.find((c) => c.id === categoryId) ?? null;
  return {
    root,
    category,
    entityId: category?.id ?? root.id,
    label: category?.label ?? root.label,
    description: category?.description || root.description,
    fields: root.fields,
    // a category's attrs already include its parents' (the API resolves the lineage)
    attrs: uniqueById([...root.attrs, ...(category?.attrs ?? [])]).filter(
      (attr) => !root.fields.some((f) => f.id === attr.id),
    ),
  };
}

export function viewFields(view: EntityView): FieldOut[] {
  return [...view.fields, ...view.attrs];
}

/** Every entity a file can be imported into: each root, then each of its categories. */
export interface ImportTarget {
  id: string;
  label: string;
  /** "SKU › Spare Part" for a category, the plain label for a root. */
  path: string;
  view: EntityView;
}

export function importTargets(entities: readonly EntityOut[]): ImportTarget[] {
  return entities.flatMap((root) => {
    const own = resolveView(entities, root.id, null);
    const targets: ImportTarget[] = own ? [{ id: root.id, label: root.label, path: root.label, view: own }] : [];
    for (const category of root.categories) {
      const view = resolveView(entities, root.id, category.id);
      if (view) targets.push({ id: category.id, label: category.label, path: `${root.label} › ${category.label}`, view });
    }
    return targets;
  });
}

/**
 * The view a URL asks for. `entity` may name a root (with an optional `category`) or a
 * category directly, as links from Import and Approvals do; anything unknown falls back to
 * the first root.
 */
export function locateView(
  entities: readonly EntityOut[],
  entity: string | null | undefined,
  category: string | null | undefined,
): EntityView | null {
  if (entity) {
    if (entities.some((e) => e.id === entity)) return resolveView(entities, entity, category);
    const owner = entities.find((e) => e.categories.some((c) => c.id === entity));
    if (owner) return resolveView(entities, owner.id, entity);
  }
  return resolveView(entities, null, null);
}

/** Find the view behind an entity id, root or category. */
export function viewForEntityId(entities: readonly EntityOut[], entityId: string): EntityView | null {
  for (const root of entities) {
    if (root.id === entityId) return resolveView(entities, root.id, null);
    if (root.categories.some((c) => c.id === entityId)) return resolveView(entities, root.id, entityId);
  }
  return null;
}

/** Label of the category a listed row belongs to (`_entity`), the root's own label otherwise. */
export function familyLabel(root: EntityOut, entityKey: unknown): string | null {
  if (typeof entityKey !== "string" || entityKey === "") return null;
  if (entityKey === root.id) return root.label;
  return root.categories.find((c) => c.id === entityKey)?.label ?? entityKey;
}

/** Category of a row (from `_entity`), or null for a row of the root itself. */
export function categoryOf(root: EntityOut, row: Readonly<Record<string, unknown>>): CategoryOut | null {
  const key = row[ENTITY_KEY];
  if (typeof key !== "string" || key === root.id) return null;
  return root.categories.find((c) => c.id === key) ?? null;
}

export const isNumericField = (field: Pick<FieldOut, "type">) => field.type === "int" || field.type === "float";

/** The unit to print after a header, unless the label already names it ("Lead Time (days)"). */
export function unitSuffix(field: Pick<FieldOut, "label" | "unit">): string | null {
  const unit = field.unit?.trim();
  if (!unit) return null;
  return field.label.toLowerCase().includes(`(${unit.toLowerCase()})`) ? null : unit;
}

export type FilterKind = "enum" | "bool" | "number" | "date" | "text";

/** The control a field's filter uses. The API compares for equality, so text is exact. */
export function filterKind(field: Pick<FieldOut, "type" | "enum_values">): FilterKind {
  if (field.type === "enum" && field.enum_values.length > 0) return "enum";
  if (field.type === "bool") return "bool";
  if (isNumericField(field)) return "number";
  if (field.type === "date") return "date";
  return "text";
}

/** Client-side check of a filter value; null when acceptable. The server re-validates. */
export function filterValueProblem(field: Pick<FieldOut, "type" | "enum_values">, raw: string): "required" | "number" | "integer" | "date" | "option" | null {
  const value = raw.trim();
  if (value === "") return "required";
  switch (filterKind(field)) {
    case "number": {
      const n = Number(value);
      if (!Number.isFinite(n)) return "number";
      return field.type === "int" && !Number.isInteger(n) ? "integer" : null;
    }
    case "date":
      return /^\d{4}-\d{2}-\d{2}$/.test(value) ? null : "date";
    case "enum":
      return field.enum_values.includes(value) ? null : "option";
    case "bool":
      return value === "true" || value === "false" ? null : "option";
    default:
      return null;
  }
}

/** Only filters on fields the current view has (a stale URL key would be a 400). */
export function filtersFor(filters: RecordFilters, fields: readonly Pick<FieldOut, "id">[]): Record<string, string[]> {
  const known = new Set(fields.map((f) => f.id));
  const out: Record<string, string[]> = {};
  for (const [key, values] of Object.entries(filters)) {
    const kept = [...new Set(values.map((v) => v.trim()).filter(Boolean))];
    if (known.has(key) && kept.length > 0) out[key] = kept;
  }
  return out;
}

export function addFilterValue(filters: RecordFilters, fieldId: string, value: string): Record<string, string[]> {
  const current = filters[fieldId] ?? [];
  const next = value.trim();
  if (!next || current.includes(next)) return { ...filters } as Record<string, string[]>;
  return { ...filters, [fieldId]: [...current, next] } as Record<string, string[]>;
}

export function removeFilterValue(filters: RecordFilters, fieldId: string, value?: string): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [key, values] of Object.entries(filters)) {
    if (key !== fieldId) out[key] = [...values];
    else if (value !== undefined) {
      const kept = values.filter((v) => v !== value);
      if (kept.length > 0) out[key] = kept;
    }
  }
  return out;
}

export type SortDirection = "asc" | "desc";
export interface FieldSort {
  columnId: string;
  direction: SortDirection;
}

/** `{columnId: "unit_cost", direction: "desc"}` → `-unit_cost` (the API's sort syntax). */
export function sortParam(sort: FieldSort | null | undefined): string | undefined {
  if (!sort) return undefined;
  return sort.direction === "desc" ? `-${sort.columnId}` : sort.columnId;
}

export function parseSort(raw: string | null | undefined): FieldSort | null {
  const value = raw?.trim();
  if (!value) return null;
  const desc = value.startsWith("-");
  const columnId = desc ? value.slice(1) : value;
  return columnId ? { columnId, direction: desc ? "desc" : "asc" } : null;
}

/** Data window state as it lives in the URL (shareable, and what Import links to). */
export interface DataLocation {
  entity: string | null;
  category: string | null;
  sort: FieldSort | null;
  page: number;
  filters: Record<string, string[]>;
}

/** Filter params carry a prefix so a field id can never collide with `entity`, `page`… */
const FILTER_PREFIX = "f.";

export function readDataLocation(params: URLSearchParams): DataLocation {
  const filters: Record<string, string[]> = {};
  for (const [key, value] of params.entries()) {
    if (!key.startsWith(FILTER_PREFIX) || !value.trim()) continue;
    const field = key.slice(FILTER_PREFIX.length);
    if (field) filters[field] = [...(filters[field] ?? []), value.trim()];
  }
  const page = Number.parseInt(params.get("page") ?? "", 10);
  return {
    entity: params.get("entity") || null,
    category: params.get("category") || null,
    sort: parseSort(params.get("sort")),
    page: Number.isFinite(page) && page > 0 ? page : 1,
    filters,
  };
}

export function dataSearch(location: Partial<DataLocation>): string {
  const params = new URLSearchParams();
  if (location.entity) params.set("entity", location.entity);
  if (location.category) params.set("category", location.category);
  const sort = sortParam(location.sort);
  if (sort) params.set("sort", sort);
  if (location.page && location.page > 1) params.set("page", String(location.page));
  for (const [field, values] of Object.entries(location.filters ?? {})) {
    for (const value of values) params.append(`${FILTER_PREFIX}${field}`, value);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** Where the Data window shows an entity's records (root, or root + category tab). */
export function dataHref(entities: readonly EntityOut[], entityId: string): string {
  const view = viewForEntityId(entities, entityId);
  if (!view) return `/data${dataSearch({ entity: entityId })}`;
  return `/data${dataSearch({ entity: view.root.id, category: view.category?.id ?? null })}`;
}
