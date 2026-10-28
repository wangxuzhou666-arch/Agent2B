"use client";

import type { Route } from "next";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { StatusBadge, type Tone } from "@/components/common/status-badge";
import { useApprovals } from "@/components/providers/approvals-provider";
import { canAct, isBusy, isResolved, type ApprovalState, type Deadline } from "@/lib/approvals";
import { cn } from "@/lib/utils";

import { ApprovalActions } from "./approval-actions";
import { AuditLinks, OutcomeView, Receipt, useFailureText, useOutcomeLine } from "./approval-outcome";
import { Countdown } from "./countdown";
import { useApproval } from "./use-approval";

export interface ApprovalCardProps {
  grantId: string;
  /** The request in words: the server's summary of exactly what will run. */
  summary: string;
  /** Headline while it waits: "Approval required" (Hub, Import), "Write request · create_po" (Approvals). */
  title: ReactNode;
  /** Kept under the receipt once resolved, so the card stays findable by what it was (Approvals). */
  subject?: ReactNode;
  /** Pills after the title: who asked, the routing arm. */
  badges?: ReactNode;
  /** One line on why it waits. */
  why?: ReactNode;
  /** More key–value rows between the request and the grant id. */
  details?: readonly { label: string; value: ReactNode }[];
  deadline: Deadline;
  /** The signed-in user: the receipt's "by" for what they resolved here. */
  userId: string;
  /** Only the requester may approve; anyone who can see it may reject. */
  canApprove?: boolean;
  /** False: a static card (an older Hub turn) with no buttons. */
  interactive?: boolean;
  /** A quiet line beside the countdown ("Only the requester can approve…"). */
  hint?: ReactNode;
  /** A link beside the countdown ("Open Approvals"). */
  link?: ReactNode;
  /** A line under the footer. */
  footnote?: ReactNode;
  /** What an expired one says; Approvals adds that it leaves the list. */
  expiredNote?: string;
}

interface Resolution {
  tone: Tone;
  label: string;
  by?: string | null;
  at?: number;
  /** One line under the summary; absent when the outcome view says it. */
  note: string | null;
  /** For screen readers, when it resolves while on screen. */
  announcement: string;
}

function useResolution(userId: string, expiredNote: string | undefined): (state: ApprovalState) => Resolution | null {
  const t = useTranslations("approval");
  const status = useTranslations("status");
  const outcomeLine = useOutcomeLine();
  return (state) => {
    const say = (label: string, note: string) => `${label}. ${note}`;
    switch (state.phase) {
      case "approved": {
        const label = status("approved");
        return { tone: "success", label, by: state.by ?? userId, at: state.at, note: null, announcement: say(label, outcomeLine(state.outcome)) };
      }
      case "rejected": {
        const label = status("rejected");
        return { tone: "destructive", label, by: state.by ?? userId, at: state.at, note: t("rejectedNote"), announcement: say(label, t("rejectedNote")) };
      }
      case "expired": {
        const label = status("expired");
        const note = expiredNote ?? t("expired");
        return { tone: "neutral", label, note, announcement: say(label, note) };
      }
      case "elsewhere": {
        const label = t("elsewhere");
        return { tone: "neutral", label, note: t("elsewhereNote"), announcement: say(label, t("elsewhereNote")) };
      }
      case "unconfirmed": {
        const label = state.started ? status("started") : t("notRun");
        const note = state.started ? t("unconfirmedStarted") : t("unconfirmedNone");
        return { tone: state.started ? "warning" : "neutral", label, note, announcement: say(label, note) };
      }
      default:
        return null;
    }
  };
}

const linkClass = "underline underline-offset-2 hover:text-foreground";

/**
 * The one approval card (DESIGN 10.6), used by Hub, Approvals and Import: what will run, a
 * countdown to the server's expiry, and Reject / Approve inline. Approving is the one-time
 * confirmation; there is no second dialog. Once resolved the receipt is its headline, followed by
 * what the run produced. State comes from the shared approval store, so a confirmation resolved
 * in one window reads the same in every other.
 */
export function ApprovalCard({
  grantId,
  summary,
  title,
  subject,
  badges,
  why,
  details,
  deadline,
  userId,
  canApprove = true,
  interactive = true,
  hint,
  link,
  footnote,
  expiredNote,
}: ApprovalCardProps) {
  const t = useTranslations("approval");
  const ta = useTranslations("approvals");
  const { refresh } = useApprovals();
  const { state, approve, reject } = useApproval(grantId);
  const describeFailure = useFailureText();
  const resolution = useResolution(userId, expiredNote)(state);
  const headingId = useId();
  const sectionRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  // announce only what happens while the card is on screen, not what it already was when it came
  const [firstPhase] = useState(state.phase);
  const changed = state.phase !== firstPhase;

  // after an action resolves it, its button is gone: focus moves to the heading, which says how it ended
  const previous = useRef(state);
  useEffect(() => {
    const wasBusy = isBusy(previous.current);
    previous.current = state;
    if (!wasBusy || !isResolved(state)) return;
    const active = document.activeElement;
    if (active === null || active === document.body || sectionRef.current?.contains(active)) {
      headingRef.current?.focus({ preventScroll: true });
    }
  }, [state]);

  // the same node in both layouts (keyed), so it is a live region before its text changes
  const live = (
    <p key="live" role="status" className="sr-only">
      {!changed ? "" : resolution ? resolution.announcement : state.phase === "checking" ? t("checkingNote") : ""}
    </p>
  );

  if (resolution) {
    return (
      <section
        ref={sectionRef}
        aria-labelledby={headingId}
        data-phase={state.phase}
        className="space-y-3 rounded-xl bg-card p-4 shadow-sm ring-1 ring-foreground/10"
      >
        <h3 id={headingId} ref={headingRef} tabIndex={-1} className="text-sm font-medium text-foreground outline-none">
          <Receipt tone={resolution.tone} label={resolution.label} by={resolution.by} at={resolution.at} />
          {subject ? <span className="mt-1.5 block">{subject}</span> : null}
        </h3>
        <p className="text-sm leading-relaxed break-words text-muted-foreground tabular-nums">{summary}</p>
        {state.phase === "approved" ? (
          <OutcomeView outcome={state.outcome} elapsedMs={state.elapsedMs} source={state.source} />
        ) : resolution.note ? (
          <p className="text-xs leading-relaxed text-faint">{resolution.note}</p>
        ) : null}
        <AuditLinks grantId={grantId} state={state} />
        {live}
      </section>
    );
  }

  const failure = state.phase === "failed" ? state.error : null;
  const auditTrail = `/audit?grant_id=${encodeURIComponent(grantId)}` as Route;
  return (
    <section
      ref={sectionRef}
      aria-labelledby={headingId}
      data-phase={state.phase}
      className={cn("rounded-xl bg-card p-4 shadow-sm ring-1", canApprove ? "ring-warning/40" : "ring-foreground/10")}
    >
      <div className="flex flex-wrap items-start gap-2">
        <h3 id={headingId} ref={headingRef} tabIndex={-1} className="min-w-0 flex-1 text-sm font-medium text-foreground outline-none">
          {title}
        </h3>
        {badges}
        <StatusBadge tone="warning">{t("write")}</StatusBadge>
      </div>
      {why ? <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{why}</p> : null}
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-caption text-muted-foreground">{t("request")}</dt>
        <dd className="text-sm break-words text-foreground tabular-nums">{summary}</dd>
        {details?.map((row) => [
          <dt key={`${row.label}-dt`} className="text-caption text-muted-foreground">
            {row.label}
          </dt>,
          <dd key={`${row.label}-dd`} className="text-sm text-foreground tabular-nums">
            {row.value}
          </dd>,
        ])}
        <dt className="text-caption text-muted-foreground">{t("grant")}</dt>
        <dd className="font-mono text-xs break-all text-faint">{grantId}</dd>
      </dl>
      {failure ? (
        <p role="alert" className="mt-3 text-xs leading-relaxed text-destructive">
          {describeFailure(failure)}
          {state.phase === "failed" && state.inDoubt && !failure.retryable ? (
            <>
              {" "}
              <Link href={auditTrail} className={linkClass}>
                {ta("viewAuditTrail")}
              </Link>
            </>
          ) : null}
        </p>
      ) : null}
      {state.phase === "checking" ? <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{t("checkingNote")}</p> : null}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Countdown
          deadline={deadline}
          // time is up on this page's clock: ask the server, whose list or 410 is the verdict
          onEnd={() => {
            if (canAct(state)) refresh();
          }}
        />
        {link}
        {hint ? <span className="text-xs text-faint">{hint}</span> : null}
        {interactive ? (
          <ApprovalActions
            className="ml-auto"
            state={state}
            canApprove={canApprove}
            onApprove={() => void approve()}
            onReject={() => void reject()}
          />
        ) : null}
      </div>
      {footnote ? <p className="mt-2 text-xs text-faint">{footnote}</p> : null}
      {live}
    </section>
  );
}
