"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

import { ApprovalCard } from "@/components/approval/approval-card";
import { useApprovalState } from "@/components/approval/use-approval";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { useApprovals } from "@/components/providers/approvals-provider";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiResource } from "@/hooks/use-api-resource";
import { api, isAbortError } from "@/lib/api";
import { arrivalNow, deadlineIn, isResolved, type ApprovalState } from "@/lib/approvals";
import { MAX_UPLOAD_BYTES, UPLOAD_EXTENSIONS } from "@/lib/config";
import { importTargets } from "@/lib/entities";
import { afterApproval, checkFile, initialWizard, uploadIssue, wizardReducer, type WizardState } from "@/lib/imports";

import { DoneStep } from "./done-step";
import { ImportSteps } from "./import-steps";
import { PreviewStep } from "./preview-step";
import { UploadStep } from "./upload-step";

type ConfirmState = Extract<WizardState, { step: "confirm" }>;

/** Step 3: the one-time confirmation for exactly this file and validation (the shared approval card). */
function ConfirmStep({
  state,
  approval,
  userId,
  onRestart,
}: {
  state: ConfirmState;
  approval: ApprovalState;
  userId: string;
  onRestart: (notice: "expired" | null) => void;
}) {
  const t = useTranslations("import.confirm");
  const ta = useTranslations("approval");
  // once listed, its words are the list's: worded again in the console's language after a switch
  const { items } = useApprovals();
  const listed = items?.find((item) => item.grant_id === state.approval.grant_id);
  const label = (listed?.label ?? state.approval.label).trim();
  // what is left here once it can no longer be used: the card says why, the wizard offers a fresh upload
  const ended = isResolved(approval) || (approval.phase === "failed" && !approval.error.retryable);
  return (
    <div className="space-y-4">
      <p className="text-sm leading-relaxed text-muted-foreground">{t("lead")}</p>
      <ApprovalCard
        grantId={state.approval.grant_id}
        summary={listed?.summary ?? state.approval.summary}
        title={label ? `${ta("title")} · ${label}` : ta("title")}
        why={ta("why")}
        deadline={state.deadline}
        userId={userId}
        link={
          <Link href="/approvals" className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
            {ta("openApprovals")}
          </Link>
        }
      />
      {ended ? (
        <Button variant="outline" size="sm" onClick={() => onRestart(approval.phase === "expired" ? "expired" : null)}>
          {t("uploadAgain")}
        </Button>
      ) : (
        <p className="text-xs text-faint">{t("footnote")}</p>
      )}
    </div>
  );
}

/**
 * The import wizard (DESIGN 10.9): Upload → Preview → Confirm → Done. Preview writes nothing;
 * Confirm is the same one-time approval every write needs, bound to the previewed bytes.
 *
 * Targets and the preview are worded by the server in the console's language, so a language
 * switch fetches the targets again and previews the open file again (same bytes, same digest).
 */
export function ImportWizard() {
  const t = useTranslations("import");
  const common = useTranslations("common");
  const locale = useLocale();
  const params = useSearchParams();
  const { refresh } = useApprovals();
  const { session } = useAuth();
  const entities = useApiResource(`entities:${locale}`, (signal) => api.listEntities(signal));
  const targets = useMemo(() => importTargets(entities.data?.items ?? []), [entities.data]);
  const [state, dispatch] = useReducer(wizardReducer, params.get("entity"), initialWizard);
  const approval = useApprovalState(state.step === "confirm" ? state.approval.grant_id : null);
  // the confirmation settled: move on in this same render, so the card never shows a state the wizard leaves
  if (state.step === "confirm") {
    const next = afterApproval(approval);
    if (next) dispatch(next);
  }
  const uploading = useRef<AbortController | null>(null);
  /** The file of the preview on screen, to preview it again in another language. */
  const previewed = useRef<File | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const firstStep = useRef(true);
  const latest = useRef(state);
  useEffect(() => {
    latest.current = state;
  });

  const entityId =
    state.entityId && targets.some((x) => x.id === state.entityId) ? state.entityId : (targets[0]?.id ?? null);
  const target = targets.find((x) => x.id === entityId) ?? null;

  // move focus to the new step's heading, so keyboard and screen-reader users follow along
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false;
      return;
    }
    heading.current?.focus();
  }, [state.step]);

  useEffect(() => () => uploading.current?.abort(), []);

  // The language changed: the preview on screen (column labels, row problems) is in the old one.
  // Preview the same file again; the one on screen stays until the new one is in, and stays for
  // good if that fails (same bytes, same verdicts, only other words).
  const shownIn = useRef(locale);
  useEffect(() => {
    if (shownIn.current === locale) return;
    shownIn.current = locale;
    const open = latest.current;
    const file = previewed.current;
    if (open.step !== "preview" || open.committing || !file) return;
    uploading.current?.abort();
    const controller = new AbortController();
    uploading.current = controller;
    api.previewImport(open.entityId, file, controller.signal).then(
      (preview) => dispatch({ type: "previewAgain", preview }),
      () => undefined,
    );
  }, [locale]);

  const upload = useCallback(
    async (file: File) => {
      if (!entityId) return;
      const problem = checkFile(file, MAX_UPLOAD_BYTES, UPLOAD_EXTENSIONS);
      if (problem) {
        dispatch({ type: "uploadFail", issue: problem });
        return;
      }
      dispatch({ type: "selectEntity", entityId });
      dispatch({ type: "uploadStart" });
      uploading.current?.abort();
      const controller = new AbortController();
      uploading.current = controller;
      try {
        const preview = await api.previewImport(entityId, file, controller.signal);
        previewed.current = file;
        dispatch({ type: "uploadOk", file: { name: file.name, size: file.size }, preview });
      } catch (error) {
        if (isAbortError(error)) return;
        dispatch({ type: "uploadFail", issue: uploadIssue(error) });
      }
    },
    [entityId],
  );

  const commit = useCallback(async () => {
    if (state.step !== "preview") return;
    uploading.current?.abort(); // a preview-again still in flight: the commit names these bytes by their digest
    dispatch({ type: "commitStart" });
    try {
      const out = await api.commitImport(state.entityId, { digest: state.preview.digest });
      if (out.status === "needs_confirmation" && out.approval) {
        // the countdown runs from now, on the monotonic clock, for the time the server says is left
        dispatch({
          type: "needsConfirmation",
          approval: out.approval,
          deadline: deadlineIn(out.approval.expires_in_s * 1000, arrivalNow()),
        });
        refresh();
      } else if (out.status === "ok" && out.result) {
        dispatch({ type: "done", result: out.result });
      } else {
        dispatch({ type: "commitFail", issue: { key: "generic", detail: null, code: "unexpected_response" } });
      }
    } catch (error) {
      dispatch({ type: "commitFail", issue: uploadIssue(error) });
    }
  }, [state, refresh]);

  if (entities.error && !entities.data) return <ErrorState error={entities.error} onRetry={entities.reload} />;
  if (!entities.data) {
    return (
      <div role="status" aria-busy="true" className="space-y-3 rounded-xl bg-card p-6 shadow-sm ring-1 ring-foreground/10">
        <span className="sr-only">{common("loading")}</span>
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-36 w-full" />
      </div>
    );
  }
  if (targets.length === 0) return <EmptyState title={t("noTargetsTitle")} description={t("noTargetsBody")} />;

  return (
    <div className="w-full max-w-5xl space-y-4">
      <ImportSteps current={state.step} />
      <section aria-labelledby="import-step-title" className="rounded-xl bg-card p-5 shadow-sm ring-1 ring-foreground/10 sm:p-6">
        <h2
          id="import-step-title"
          ref={heading}
          tabIndex={-1}
          className="mb-4 text-base font-semibold text-foreground outline-none"
        >
          {t(`stepTitle.${state.step}`)}
        </h2>
        {state.step === "upload" ? (
          <UploadStep
            targets={targets}
            entityId={entityId}
            busy={state.busy}
            issue={state.issue}
            notice={state.notice}
            onSelectEntity={(id) => dispatch({ type: "selectEntity", entityId: id })}
            onFile={(file) => void upload(file)}
          />
        ) : state.step === "preview" ? (
          <PreviewStep
            target={target}
            file={state.file}
            preview={state.preview}
            committing={state.committing}
            issue={state.issue}
            onCommit={() => void commit()}
            onCancel={() => dispatch({ type: "restart" })}
          />
        ) : state.step === "confirm" ? (
          <ConfirmStep
            state={state}
            approval={approval}
            userId={session?.userId ?? ""}
            onRestart={(notice) => dispatch({ type: "restart", notice })}
          />
        ) : (
          <DoneStep target={target} file={state.file} result={state.result} onAgain={() => dispatch({ type: "restart" })} />
        )}
      </section>
    </div>
  );
}
