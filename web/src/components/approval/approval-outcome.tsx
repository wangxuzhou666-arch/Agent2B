"use client";

import { Check } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Markdown } from "@/components/common/markdown";
import { ProvenanceLine } from "@/components/common/provenance-line";
import { ResultTable } from "@/components/common/result-table";
import { StatusBadge, type Tone } from "@/components/common/status-badge";
import { TraceList } from "@/components/common/trace-list";
import type { ApprovalOutcome, Failure } from "@/lib/api";
import { AUDIT_UNREADABLE, type ApprovalState, type OutcomeSource } from "@/lib/approvals";
import { formatDateTime } from "@/lib/format";
import { modelFailure, type ModelCallError } from "@/lib/trace";
import { cn } from "@/lib/utils";

function clockTime(ms: number): string {
  return formatDateTime(new Date(ms).toISOString())?.slice(11) ?? "";
}

/** How a confirmation ended, as one line: tone pill + "by alice · 14:02". The card's headline. */
export function Receipt({ tone, label, by, at }: { tone: Tone; label: string; by?: string | null; at?: number }) {
  const t = useTranslations("approval");
  return (
    <span className="flex flex-wrap items-center gap-2">
      <StatusBadge tone={tone}>{label}</StatusBadge>
      {/* a space for its accessible name ("Approved by alice · 14:02"); a flex gap is not one */}{" "}
      {by && at !== undefined && Number.isFinite(at) ? (
        <span className="text-xs font-normal text-faint tabular-nums">{t("receiptBy", { user: by, time: clockTime(at) })}</span>
      ) : null}
    </span>
  );
}

/** The run's verdict in words: "The write ran." or why it didn't. */
export function useOutcomeLine(): (outcome: ApprovalOutcome) => string {
  const t = useTranslations("approval");
  return (outcome) => {
    const problem = outcome.problem ?? t("outcomeUnknown");
    switch (outcome.status) {
      case "ok":
        return t("outcomeOk");
      case "blocked":
        return t("outcomeBlocked", { problem });
      case "invalid":
        return t("outcomeInvalid", { problem });
      default:
        return t("outcomeError", { problem });
    }
  };
}

/**
 * The line under an answer (the hub's, or an approved run's) when model calls failed: which
 * calls, and what stood in for them. Warning text, as for an import's skipped rows: a configured
 * model that fails must not read like one that answered, nor like no model at all. Nothing when
 * every call succeeded or none was made.
 */
export function ModelFailureNote({
  errors,
  answerSource,
  className,
}: {
  errors: readonly ModelCallError[] | undefined;
  answerSource: ApprovalOutcome["answer_source"];
  className?: string;
}) {
  const t = useTranslations();
  const failure = modelFailure(errors, answerSource);
  if (!failure) return null;
  const calls = failure.calls.map((call) => t("hub.modelCall", { call })).join(t("trace.itemSeparator"));
  return (
    <p className={cn("text-xs text-warning", className)}>
      {t("hub.modelFailed", { count: failure.calls.length, calls, fallback: failure.fallback })}
    </p>
  );
}

/**
 * What an approved run produced, inside the card (DESIGN 7: no chat-style answer in a card): the
 * verdict and the narration as report prose in a muted inset, then its rows, then its trace,
 * collapsed.
 */
export function OutcomeView({
  outcome,
  elapsedMs,
  source,
}: {
  outcome: ApprovalOutcome;
  elapsedMs: number | null;
  source: OutcomeSource;
}) {
  const t = useTranslations();
  const line = useOutcomeLine()(outcome);
  const answer = outcome.answer.trim();
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-muted/40 px-3 py-2">
        {outcome.status === "ok" ? (
          <p className="flex items-center gap-1.5 text-sm font-medium text-success">
            <Check aria-hidden="true" className="size-4 stroke-[2.5]" />
            {line}
          </p>
        ) : (
          <p className="text-sm leading-relaxed font-medium break-words text-destructive">{line}</p>
        )}
        {answer ? (
          <Markdown className="mt-1.5" source={outcome.answer_source}>
            {answer}
          </Markdown>
        ) : null}
        {outcome.narrative_stripped > 0 ? (
          <p className="mt-1.5 text-xs text-faint">{t("hub.numericLock", { count: outcome.narrative_stripped })}</p>
        ) : null}
        <ModelFailureNote errors={outcome.model_errors} answerSource={outcome.answer_source} className="mt-1.5" />
        {source === "audit" ? <p className="mt-1.5 text-xs text-faint">{t("approval.fromAudit")}</p> : null}
      </div>
      {outcome.columns.length > 0 ? (
        <ResultTable columns={outcome.columns} rows={outcome.rows} provenance={outcome.provenance} />
      ) : outcome.provenance ? (
        <ProvenanceLine provenance={outcome.provenance} />
      ) : null}
      {outcome.trace.length > 0 ? <TraceList steps={outcome.trace} elapsedMs={elapsedMs} kind="approval" /> : null}
    </div>
  );
}

const linkClass = "text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline";

/** Where the record of it lives: the run's audit record, or the confirmation's trail; the imported rows. */
export function AuditLinks({ grantId, state }: { grantId: string; state: ApprovalState }) {
  const t = useTranslations("approvals");
  const auditId = state.phase === "approved" ? state.outcome.audit_ids.at(-1) : undefined;
  const imported = state.phase === "approved" ? state.outcome.import_result : null;
  const auditHref = (auditId !== undefined ? `/audit?id=${auditId}` : `/audit?grant_id=${encodeURIComponent(grantId)}`) as Route;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <Link href={auditHref} className={linkClass}>
        {auditId !== undefined ? t("viewAuditRecord", { id: auditId }) : t("viewAuditTrail")}
      </Link>
      {imported ? (
        <Link href={`/data?entity=${encodeURIComponent(imported.entity)}` as Route} className={linkClass}>
          {t("viewImported")}
        </Link>
      ) : null}
    </div>
  );
}

/** Why approving or rejecting failed, in words: lost in transit, or the server's verdict. */
export function useFailureText(): (failure: Failure) => string {
  const t = useTranslations("approval");
  return (failure) => {
    if (failure.code === AUDIT_UNREADABLE) return t("errorAudit");
    if (failure.retryable) return t("errorTransport");
    switch (failure.status) {
      case 409:
        return t("errorConflict");
      case 403:
        return t("errorForbidden");
      case 404:
        return t("errorNotFound");
      case 410:
        return t("errorExpired");
      default:
        return t("errorGeneric", { message: failure.message });
    }
  };
}
