"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { ApprovalCard } from "@/components/approval/approval-card";
import { RouteSourceBadge } from "@/components/common/route-source-badge";
import { StatusBadge } from "@/components/common/status-badge";
import { ToolLabel } from "@/components/common/tool-label";
import type { ApprovalItem } from "@/lib/api";
import { listedDeadline, type Arrival } from "@/lib/approvals";
import { formatTimestamp } from "@/lib/format";

const IMPORT_PREFIX = "import:";

/** `import:spare_part` → `spare_part`: the kind pill already says it is an import. */
export function displayTarget(item: Pick<ApprovalItem, "kind" | "target">): string {
  return item.kind === "import" && item.target.startsWith(IMPORT_PREFIX)
    ? item.target.slice(IMPORT_PREFIX.length)
    : item.target;
}

/**
 * One confirmation on the Approvals window (DESIGN 10.6): what will run, who asked, when, through
 * which routing arm, and a countdown to the server's expiry. The requester may approve; anyone
 * allowed to see it (the requester, or an admin) may reject. `arrival`: when the list it came in
 * arrived, which the countdown counts from (with the time left the server gave in that list).
 *
 * The headline names it as the reader's language does, then by the id the audit log records:
 * "Write request · Create purchase order (create_po)"; an import's label already says what it
 * is ("Import Packaging (packaging)").
 */
export function ApprovalItemCard({ item, arrival, userId }: { item: ApprovalItem; arrival: Arrival; userId: string }) {
  const t = useTranslations("approvals");
  const kind = item.kind === "import" ? t("kindImport") : t("kindWrite");
  const named = <ToolLabel label={item.label} id={displayTarget(item)} />;
  const title =
    item.kind === "import" && item.label.trim() ? (
      named
    ) : (
      <>
        {kind} · {named}
      </>
    );
  const deadline = useMemo(() => listedDeadline(item, arrival), [item, arrival]);
  return (
    <ApprovalCard
      grantId={item.grant_id}
      summary={item.summary}
      title={title}
      subject={title}
      badges={
        <>
          {!item.mine ? <StatusBadge tone="info">{t("requestedBy", { user: item.user_id })}</StatusBadge> : null}
          {item.route_source ? <RouteSourceBadge source={item.route_source} /> : null}
        </>
      }
      details={[
        { label: t("requester"), value: item.mine ? t("you", { user: item.user_id }) : item.user_id },
        { label: t("requested"), value: formatTimestamp(item.created_at) ?? "—" },
      ]}
      deadline={deadline}
      userId={userId}
      canApprove={item.mine}
      hint={!item.mine ? t("rejectOnly") : undefined}
      expiredNote={t("expiredNote")}
    />
  );
}
