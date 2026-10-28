"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { ApprovalCard } from "@/components/approval/approval-card";
import { ModelFailureNote } from "@/components/approval/approval-outcome";
import { useApprovalState } from "@/components/approval/use-approval";
import { Markdown } from "@/components/common/markdown";
import { ProvenanceLine } from "@/components/common/provenance-line";
import { ResultTable } from "@/components/common/result-table";
import { ToolLabel } from "@/components/common/tool-label";
import { TraceList } from "@/components/common/trace-list";
import { useAuth } from "@/components/providers/auth-provider";
import type { TraceStep } from "@/lib/api";
import { exploreStop } from "@/lib/trace";
import { cn } from "@/lib/utils";

import { ClarifyChips } from "./clarify-chips";
import { useConversation } from "./conversation-provider";
import type { Turn, TurnError } from "./conversation-state";

function UserBubble({ query, explicitTarget }: { query: string; explicitTarget: string | null }) {
  const t = useTranslations("hub");
  const common = useTranslations("common");
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-xl rounded-br-sm bg-secondary px-3.5 py-2 text-answer leading-relaxed font-medium break-words whitespace-pre-wrap text-secondary-foreground">
        <span className="sr-only">{common("labelPrefix", { label: t("you") })}</span>
        {query}
        {explicitTarget ? (
          <span className="mt-1 block text-xs font-normal text-muted-foreground">
            {t("ranAs", { target: explicitTarget })}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function ErrorLine({ text, onRetry }: { text: string; onRetry?: () => void }) {
  const t = useTranslations("common");
  return (
    <div role="alert" className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <p className="text-nav leading-relaxed text-destructive">{text}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-sm text-xs text-muted-foreground underline-offset-2 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          {t("retry")}
        </button>
      ) : null}
    </div>
  );
}

/**
 * Why an exploration run stopped short, from the trace step that ended it: amber for a spent
 * budget, red for a failure or a refusal. A run that answered or waits for an approval says
 * nothing here. The answer itself already leads with the stop; this is its label.
 */
function StopReasonLine({ steps }: { steps: readonly TraceStep[] }) {
  const t = useTranslations("hub");
  const common = useTranslations("common");
  const stop = exploreStop(steps);
  if (!stop) return null;
  return (
    <p
      data-stop-reason={stop.reason}
      className={cn("text-xs", stop.tone === "warn" ? "text-warning" : "text-destructive")}
    >
      <span className="font-medium">{common("labelPrefix", { label: t("stopLabel") })}</span>
      {t("stopReason", { reason: stop.reason, detail: stop.detail })}
    </p>
  );
}

function useTurnErrorText(): (error: TurnError) => string {
  const t = useTranslations("hub");
  return (error) => {
    // a gateway that could not reach the API is, to the reader, the same as no network
    if (error.kind === "network" || (error.kind === "http" && error.retryable)) return t("errorNetwork");
    if (error.kind === "timeout") return t("errorTimeout");
    if (error.code === "stream_incomplete") return t("errorIncomplete");
    return t("errorGeneric", { message: error.message });
  };
}

/**
 * One question and everything the engine sent back for it: trace, answer, an exploration's stop
 * reason, numeric-lock and failed-model-call notices, clarify chips, approval card, result table
 * and provenance.
 */
export function TurnView({ turn, latest, busy }: { turn: Turn; latest: boolean; busy: boolean }) {
  const t = useTranslations("hub");
  const ta = useTranslations("approval");
  const { send, retry } = useConversation();
  const { session } = useAuth();
  const errorText = useTurnErrorText();
  const response = turn.response;
  const streaming = turn.status === "streaming";
  const canRetry = latest && !busy;
  const approvalRef = response?.approval ?? null;
  // what waits for approval, by name (DESIGN 10.6): the approval's own target, which is set for
  // every write; the routed one only stands in for a response that lacks it, because a write the
  // exploration agent proposed has no routed target
  const approvalTarget = approvalRef?.target ?? response?.decision?.target ?? null;
  const approval = useApprovalState(approvalRef?.grant_id ?? null);
  // A pending write's system text repeats the approval card's summary; the card says it once.
  const answer = approvalRef && response?.answer_source === "system" ? "" : (response?.answer.trim() ?? "");
  const wide =
    (response?.columns.length ?? 0) > 0 || (approval.phase === "approved" && approval.outcome.columns.length > 0);
  const interactive = latest && !busy;

  return (
    <article
      // turns with a table widen together on roomy screens, so left edges stay aligned (DESIGN 5.2)
      className={cn("animate-in space-y-3 duration-300 fade-in", wide && "@min-[62rem]/hub:-mx-24")}
      aria-busy={streaming || undefined}
    >
      <UserBubble query={turn.query} explicitTarget={turn.explicitTarget} />

      <TraceList
        steps={turn.steps}
        live={streaming}
        startedAt={turn.startedAt}
        elapsedMs={turn.elapsedMs}
        outcome={turn.status === "error" ? "failed" : turn.status === "cancelled" ? "stopped" : "done"}
      />
      <p role="status" className="sr-only">
        {streaming ? t("working") : turn.status === "done" ? t("answerReady") : ""}
      </p>

      {response ? (
        <div className="space-y-3">
          {/* the server answered with a failure: a verdict, so no Retry (DESIGN 10.4) */}
          {response.error ? <ErrorLine text={t("errorGeneric", { message: response.error })} /> : null}
          {answer ? (
            <Markdown className="answer-prose ai-enter" source={response.answer_source}>
              {answer}
            </Markdown>
          ) : null}
          <StopReasonLine steps={turn.steps} />
          {response.narrative_stripped > 0 ? (
            <p className="text-xs text-faint">{t("numericLock", { count: response.narrative_stripped })}</p>
          ) : null}
          <ModelFailureNote errors={response.model_errors} answerSource={response.answer_source} />
          {response.clarify_options.length > 0 ? (
            <ClarifyChips
              options={response.clarify_options}
              interactive={latest && !busy}
              onPick={(option) =>
                send(turn.query, { explicitTarget: option.name, explicitParams: option.explicit_params ?? null })
              }
            />
          ) : null}
          {approvalRef && turn.approvalDeadline ? (
            <ApprovalCard
              grantId={approvalRef.grant_id}
              summary={approvalRef.summary}
              title={
                approvalRef.label.trim() || approvalTarget ? (
                  <>
                    {ta("title")} · <ToolLabel label={approvalRef.label} id={approvalTarget} />
                  </>
                ) : (
                  ta("title")
                )
              }
              why={ta("why")}
              deadline={turn.approvalDeadline}
              userId={session?.userId ?? ""}
              interactive={interactive}
              link={
                <Link
                  href="/approvals"
                  className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  {ta("openApprovals")}
                </Link>
              }
              footnote={interactive ? undefined : ta("notLatest")}
            />
          ) : null}
          {response.columns.length > 0 ? (
            <ResultTable columns={response.columns} rows={response.rows} provenance={response.provenance} />
          ) : response.provenance ? (
            <ProvenanceLine provenance={response.provenance} />
          ) : null}
        </div>
      ) : null}

      {turn.status === "error" && turn.error ? (
        <ErrorLine
          text={errorText(turn.error)}
          onRetry={canRetry && turn.error.retryable ? () => retry(turn.id) : undefined}
        />
      ) : null}
      {turn.status === "cancelled" ? <p className="text-xs text-faint">{t("cancelled")}</p> : null}
    </article>
  );
}
