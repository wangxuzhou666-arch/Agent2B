"use client";

import { LoaderCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import type { KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { canAct, isBusy, type ApprovalAction, type ApprovalState } from "@/lib/approvals";
import { cn } from "@/lib/utils";

/** A held Enter or Space repeats its keydown; one press is one request. */
function ignoreRepeat(event: KeyboardEvent<HTMLButtonElement>) {
  if (event.repeat && (event.key === "Enter" || event.key === " ")) event.preventDefault();
}

/**
 * Reject and Approve (DESIGN 10.6: outline, then ink). While a request is out the buttons are
 * `aria-disabled`, not `disabled`, so the pressed one keeps focus; presses are ignored either way
 * (the store sends one request at a time). A failure that was a verdict disables them for good.
 */
export function ApprovalActions({
  state,
  canApprove,
  onApprove,
  onReject,
  className,
}: {
  state: ApprovalState;
  canApprove: boolean;
  onApprove: () => void;
  onReject: () => void;
  className?: string;
}) {
  const t = useTranslations("approval");
  const busy = isBusy(state);
  const enabled = canAct(state);
  const running: ApprovalAction | null =
    state.phase === "approving" ? "approve" : state.phase === "rejecting" ? "reject" : state.phase === "checking" ? state.action : null;
  const working = (action: ApprovalAction) =>
    state.phase === "checking" ? t("checking") : action === "approve" ? t("approving") : t("rejecting");

  const press = (action: ApprovalAction) => () => {
    if (enabled) (action === "approve" ? onApprove : onReject)();
  };
  const common = {
    size: "sm" as const,
    disabled: !busy && !enabled,
    "aria-disabled": busy || undefined,
    onKeyDown: ignoreRepeat,
    className: "aria-disabled:cursor-default aria-disabled:opacity-50",
  };

  return (
    <div className={cn("flex gap-3", className)}>
      <Button variant="outline" onClick={press("reject")} {...common}>
        {running === "reject" ? working("reject") : t("reject")}
      </Button>
      {canApprove ? (
        <Button variant="ink" onClick={press("approve")} {...common}>
          {running === "approve" ? (
            <>
              <LoaderCircle aria-hidden="true" className="animate-spin" />
              {working("approve")}
            </>
          ) : (
            t("approve")
          )}
        </Button>
      ) : null}
    </div>
  );
}
