"use client";

import { Lock } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { RouteSourceBadge, useRouteSourceMeaning } from "@/components/common/route-source-badge";
import { StatusPill } from "@/components/common/status-label";
import { TraceList } from "@/components/common/trace-list";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiResource } from "@/hooks/use-api-resource";
import { api, type AuditDetailOut } from "@/lib/api";
import { isMasked, loadRequestChain } from "@/lib/audit";
import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

import type messages from "../../../messages/en.json";
import { RequestChain } from "./request-chain";

type FactKey = keyof (typeof messages)["audit"]["detail"]["field"];

function Section({ title, children, id }: { title: string; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} className="space-y-2">
      <h3 id={id} className="text-caption font-medium tracking-wide text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

/** One parameter value as recorded: masked values say so, never a placeholder that looks real. */
export function ParamValue({ value }: { value: unknown }) {
  const t = useTranslations("audit.detail");
  if (isMasked(value)) {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm bg-muted px-1.5 font-mono text-xs text-muted-foreground">
        <Lock aria-hidden="true" className="size-3" />
        {t("masked")}
        <span className="sr-only"> ({t("maskedHint")})</span>
      </span>
    );
  }
  if (value === null || value === undefined || value === "") return <span className="text-faint">—</span>;
  if (typeof value === "object") {
    return <code className="font-mono text-xs break-all text-muted-foreground">{JSON.stringify(value)}</code>;
  }
  return <span className="font-mono text-xs break-all text-foreground tabular-nums">{String(value)}</span>;
}

function Facts({ entry, onOpen }: { entry: AuditDetailOut; onOpen: (id: number) => void }) {
  const t = useTranslations("audit.detail");
  const meaningOf = useRouteSourceMeaning();
  const rows: [FactKey, ReactNode][] = [
    ["status", <StatusPill key="s" status={entry.status} />],
    [
      "route",
      entry.route_source ? (
        <span key="r" className="inline-flex flex-wrap items-center gap-2">
          <RouteSourceBadge source={entry.route_source} />
          {meaningOf(entry.route_source) ? (
            <span className="text-xs text-muted-foreground">{meaningOf(entry.route_source)}</span>
          ) : null}
        </span>
      ) : (
        <span key="r" className="text-faint">—</span>
      ),
    ],
    [
      "target",
      entry.target ? (
        <span key="t" className="font-mono text-xs">
          {entry.target}
          {entry.effect ? <span className="text-faint"> · {entry.effect}</span> : null}
        </span>
      ) : (
        <span key="t" className="text-faint">—</span>
      ),
    ],
    ["rows", entry.row_count ?? <span key="n" className="text-faint">—</span>],
    [
      "user",
      <span key="u">
        <span className="font-medium">{entry.user_id}</span>
        {entry.roles.length > 0 ? <span className="text-faint"> · {entry.roles.join(", ")}</span> : null}
      </span>,
    ],
    ["time", <span key="ts" className="tabular-nums">{formatTimestamp(entry.ts)}</span>],
  ];
  if (entry.problem) rows.push(["problem", <span key="p" className="font-mono text-xs text-destructive">{entry.problem}</span>]);
  if (entry.grant_id) rows.push(["grant", <span key="g" className="font-mono text-xs break-all">{entry.grant_id}</span>]);
  if (entry.request_id) rows.push(["request", <span key="q" className="font-mono text-xs break-all">{entry.request_id}</span>]);
  if (entry.parent_id !== null && entry.parent_id !== undefined) {
    const parent = entry.parent_id;
    rows.push([
      "parent",
      <button
        key="pa"
        type="button"
        onClick={() => onOpen(parent)}
        className="rounded-sm font-mono text-xs text-foreground underline-offset-2 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        #{parent}
      </button>,
    ]);
  }
  if (entry.params_digest) {
    rows.push([
      "digest",
      <span key="d" title={entry.params_digest} className="font-mono text-xs break-all text-faint">
        {entry.params_digest}
      </span>,
    ]);
  }
  return (
    <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-4 gap-y-1.5">
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-caption text-muted-foreground">{t(`field.${key}`)}</dt>
          <dd className="min-w-0 text-sm text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Params({ params }: { params: Readonly<Record<string, unknown>> }) {
  const t = useTranslations("audit.detail");
  const entries = Object.entries(params);
  if (entries.length === 0) return <p className="text-xs text-faint">{t("noParams")}</p>;
  return (
    <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-4 gap-y-1 rounded-md bg-muted/40 p-2.5">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
          <dd className="min-w-0">
            <ParamValue value={value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Chain({ entry, onOpen }: { entry: AuditDetailOut; onOpen: (id: number) => void }) {
  const t = useTranslations("audit.detail");
  const chain = useApiResource(`chain:${entry.id}`, (signal) =>
    loadRequestChain(entry, { list: api.listAudit, get: api.getAudit }, signal),
  );
  if (chain.error && !chain.data) return <ErrorState variant="inline" error={chain.error} onRetry={chain.reload} className="px-0 py-4 text-left" />;
  if (!chain.data || chain.stale) {
    return (
      <div role="status" aria-busy="true" className="space-y-2">
        <span className="sr-only">{t("chainLoading")}</span>
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    );
  }
  if (chain.data.length <= 1) return <p className="text-xs text-faint">{t("chainSingle")}</p>;
  return <RequestChain entries={chain.data} currentId={entry.id} onOpen={onOpen} />;
}

/**
 * Replay of one audit entry (detail sheet): the recorded facts, the parameters as stored
 * (restricted values masked at write time), the full decision trace — every step the server
 * recorded, including ones this build has no label for — and the request chain around it.
 */
export function AuditDetailSheet({
  entryId,
  onClose,
  onOpen,
}: {
  entryId: number | null;
  onClose: () => void;
  onOpen: (id: number) => void;
}) {
  const t = useTranslations("audit.detail");
  const common = useTranslations("common");
  const detail = useApiResource(entryId === null ? null : `audit:${entryId}`, (signal) =>
    api.getAudit(entryId as number, signal),
  );
  // while another entry loads, the previous one stays (dimmed) so the sheet does not jump
  const entry = entryId === null ? null : detail.data;
  const fresh = entry !== null && entry.id === entryId;

  return (
    <Sheet open={entryId !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent side="right" closeLabel={common("close")} className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl data-[side=right]:lg:max-w-2xl">
        <SheetHeader className="border-b border-border pr-12">
          <SheetTitle className="font-serif text-lg font-semibold tracking-tight">
            {t("title", { id: entryId ?? 0 })}
          </SheetTitle>
          <SheetDescription className="text-xs text-faint tabular-nums">
            {entry && fresh ? `${formatTimestamp(entry.ts)} · ${entry.user_id}` : common("loading")}
          </SheetDescription>
        </SheetHeader>
        <div
          className={cn("flex-1 space-y-6 overflow-y-auto p-4 transition-opacity", !fresh && entry && "opacity-60")}
          aria-busy={detail.loading || undefined}
        >
          {detail.error && !fresh ? (
            <ErrorState variant="inline" error={detail.error} onRetry={detail.reload} />
          ) : !entry ? (
            <div role="status" aria-busy="true" className="space-y-3">
              <span className="sr-only">{common("loading")}</span>
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : (
            <>
              <Facts entry={entry} onOpen={onOpen} />
              <Section id="audit-params" title={t("params")}>
                <Params params={entry.params} />
              </Section>
              <Section id="audit-trace" title={t("trace")}>
                {entry.trace.length > 0 ? (
                  <TraceList steps={entry.trace} defaultOpen />
                ) : (
                  <p className="text-xs text-faint">{t("noTrace")}</p>
                )}
              </Section>
              <Section id="audit-chain" title={t("chainTitle")}>
                <Chain key={entry.id} entry={entry} onOpen={onOpen} />
              </Section>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
