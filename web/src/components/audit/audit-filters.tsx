"use client";

import { Search, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { FilterChips, type FilterChip } from "@/components/common/filter-chips";
import { useRouteSourceMeaning } from "@/components/common/route-source-badge";
import { useStatusLabel } from "@/components/common/status-label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { AUDIT_FILTER_KEYS, isStatsWindow, STATS_WINDOWS, type AuditFilterKey, type AuditFilters } from "@/lib/audit";
import { cn } from "@/lib/utils";

const TEXT_DEBOUNCE_MS = 350;

/** A text filter that commits after a short pause (and at once on Enter or clear). */
function TextFilter({
  label,
  value,
  onCommit,
  mono = false,
  className,
}: {
  label: string;
  value: string;
  onCommit: (value: string) => void;
  mono?: boolean;
  className?: string;
}) {
  const t = useTranslations("filters");
  // `sent`: the last value this input committed; `seen`: the prop value at the last render
  const [draft, setDraft] = useState({ text: value, sent: value, seen: value });
  if (draft.seen !== value) {
    // the prop moved: our own commit landing keeps what is being typed; anything else
    // (a removed chip, a link into the page) replaces it
    setDraft({ text: value === draft.sent ? draft.text : value, sent: value, seen: value });
  }
  const text = draft.text;
  const commit = (next: string) => {
    setDraft((d) => ({ ...d, sent: next }));
    onCommit(next);
  };
  const commitRef = useRef(commit);
  useEffect(() => {
    commitRef.current = commit;
  });

  useEffect(() => {
    if (text.trim() === value) return;
    const timer = setTimeout(() => commitRef.current(text.trim()), TEXT_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text, value]);

  return (
    <div className={cn("relative min-w-40 flex-1 basis-full sm:max-w-56 sm:basis-44", className)}>
      <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        aria-label={label}
        placeholder={label}
        value={text}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => setDraft((d) => ({ ...d, text: e.target.value }))}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(text.trim());
        }}
        className={cn("h-8 bg-background pr-8 pl-8", mono && text && "font-mono text-xs md:text-xs")}
      />
      {text ? (
        <button
          type="button"
          aria-label={t("clearField", { field: label })}
          onClick={() => {
            setDraft((d) => ({ ...d, text: "" }));
            commit("");
          }}
          className="absolute top-1/2 right-1.5 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-faint outline-none hover:bg-foreground/10 hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <X aria-hidden="true" className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}

/**
 * Audit filter bar (DESIGN 4.11): actor, target, outcome, route source and time preset, plus
 * the request / grant ids a chain link sets. Every active filter also shows as a removable chip.
 */
export function AuditFilterBar({
  filters,
  onChange,
  statusOptions,
  sourceOptions,
}: {
  filters: AuditFilters;
  onChange: (filters: AuditFilters) => void;
  statusOptions: readonly string[];
  sourceOptions: readonly string[];
}) {
  const t = useTranslations("audit.filters");
  const tw = useTranslations("audit.stats.window");
  const statusLabel = useStatusLabel();
  const meaningOf = useRouteSourceMeaning();
  // the value stays verbatim (it is what the log's Route column shows), its meaning follows
  const sourceLabel = (source: string) => {
    const meaning = meaningOf(source);
    return meaning ? `${source} · ${meaning}` : source;
  };

  const set = (key: AuditFilterKey, value: string) => {
    const next: AuditFilters = { ...filters };
    if (value) next[key] = value;
    else delete next[key];
    onChange(next);
  };

  const display = (key: AuditFilterKey, value: string): string => {
    if (key === "status") return statusLabel(value);
    if (key === "since") return isStatsWindow(value) ? t("sinceValue", { window: tw(value) }) : value;
    return value;
  };

  const chips: FilterChip[] = AUDIT_FILTER_KEYS.flatMap((key) => {
    const value = filters[key];
    if (!value) return [];
    const hint = key === "route_source" ? (meaningOf(value) ?? undefined) : undefined;
    return [{ id: key, label: t(`label.${key}`), value: display(key, value), hint, onRemove: () => set(key, "") }];
  });

  return (
    <div className="mb-3 rounded-xl bg-card p-3 shadow-sm ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-center gap-2">
        <TextFilter label={t("label.user")} value={filters.user ?? ""} onCommit={(v) => set("user", v)} />
        <TextFilter label={t("label.target")} value={filters.target ?? ""} onCommit={(v) => set("target", v)} mono />
        <NativeSelect
          aria-label={t("label.status")}
          value={filters.status ?? ""}
          onChange={(e) => set("status", e.target.value)}
          className={cn("w-40", filters.status && "[&_select]:border-[var(--accent-foreground)]/35 [&_select]:bg-accent [&_select]:text-accent-foreground")}
        >
          <option value="">{t("anyStatus")}</option>
          {statusOptions.map((status) => (
            <option key={status} value={status}>
              {statusLabel(status)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label={t("label.route_source")}
          value={filters.route_source ?? ""}
          onChange={(e) => set("route_source", e.target.value)}
          title={filters.route_source ? (meaningOf(filters.route_source) ?? undefined) : undefined}
          className={cn("w-48", filters.route_source && "[&_select]:border-[var(--accent-foreground)]/35 [&_select]:bg-accent [&_select]:text-accent-foreground")}
        >
          <option value="">{t("anySource")}</option>
          {sourceOptions.map((source) => (
            <option key={source} value={source}>
              {sourceLabel(source)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label={t("label.since")}
          value={filters.since ?? ""}
          onChange={(e) => set("since", e.target.value)}
          className={cn("w-40", filters.since && "[&_select]:border-[var(--accent-foreground)]/35 [&_select]:bg-accent [&_select]:text-accent-foreground")}
        >
          <option value="">{t("anyTime")}</option>
          {STATS_WINDOWS.map((preset) => (
            <option key={preset} value={preset}>
              {t("sinceValue", { window: tw(preset) })}
            </option>
          ))}
        </NativeSelect>
        <TextFilter
          label={t("label.request_id")}
          value={filters.request_id ?? ""}
          onCommit={(v) => set("request_id", v)}
          mono
        />
        <TextFilter label={t("label.grant_id")} value={filters.grant_id ?? ""} onCommit={(v) => set("grant_id", v)} mono />
      </div>
      <FilterChips chips={chips} onClearAll={() => onChange({})} />
    </div>
  );
}
