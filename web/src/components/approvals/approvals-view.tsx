"use client";

import { RefreshCw, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";

import { useApprovalStates } from "@/components/approval/use-approval";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { useApprovals } from "@/components/providers/approvals-provider";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ApprovalItem } from "@/lib/api";
import type { Arrival } from "@/lib/approvals";
import { hasRole } from "@/lib/session";

import { ApprovalItemCard } from "./approval-item-card";

/**
 * How long a card that expired here stays (saying so) before it may leave, even when the
 * refreshed list no longer has it.
 */
export const EXPIRED_LINGER_MS = 4_000;

const urgency = (a: ApprovalItem, b: ApprovalItem) =>
  Number(b.mine) - Number(a.mine) || Date.parse(a.expires_at) - Date.parse(b.expires_at);

/** Mine first; within each group the one that expires soonest first (it is the most urgent). */
export function orderApprovals(items: readonly ApprovalItem[]): ApprovalItem[] {
  return [...items].sort(urgency);
}

/** A confirmation as last listed, with the arrival of that list (its time left is as of then). */
interface Listed {
  item: ApprovalItem;
  arrival: Arrival;
}

const byGrant = (items: readonly ApprovalItem[] | null, arrival: Arrival | null): Record<string, Listed> =>
  items && arrival ? Object.fromEntries(items.map((item) => [item.grant_id, { item, arrival }])) : {};

function LoadingCards() {
  const t = useTranslations("common");
  return (
    <div role="status" aria-busy="true" aria-live="polite" className="space-y-3">
      <span className="sr-only">{t("loading")}</span>
      {[0, 1].map((i) => (
        <div key={i} className="space-y-3 rounded-xl bg-card p-4 shadow-sm ring-1 ring-foreground/10">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        {description ? <p className="mt-0.5 text-xs leading-relaxed text-faint">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

/**
 * Pending confirmations (DESIGN 10.6 on a page). The list comes from the shared approvals
 * provider and each card's state from the shared approval store, so the sidebar badge, Hub and
 * this window never disagree. A card resolved while the window is open stays with its result; one
 * that expired stays a moment, saying so; one resolved elsewhere simply leaves.
 */
export function ApprovalsView() {
  const t = useTranslations("approvals");
  const pages = useTranslations("pages.approvals");
  const { items, arrival, error, refresh } = useApprovals();
  const states = useApprovalStates();
  const { session } = useAuth();
  const userId = session?.userId ?? "";
  const canWrite = hasRole(session?.roles, "operator");

  // every confirmation listed while this window is open, so one that left the list can stay
  const [seen, setSeen] = useState(() => byGrant(items, arrival));
  const [seenItems, setSeenItems] = useState(items);
  if (items !== seenItems) {
    setSeenItems(items);
    setSeen({ ...seen, ...byGrant(items, arrival) });
  }
  const [lingered, setLingered] = useState<ReadonlySet<string>>(() => new Set());

  useEffect(() => {
    refresh(); // a fresh list whenever the window opens
  }, [refresh]);

  // an expired card stays until its linger is over, then the list is refreshed
  useEffect(() => {
    const waiting = [...states].flatMap(([id, state]) =>
      state.phase === "expired" && id in seen && !lingered.has(id) ? [{ id, dueAt: state.at + EXPIRED_LINGER_MS }] : [],
    );
    if (waiting.length === 0) return;
    const dueAt = Math.min(...waiting.map((entry) => entry.dueAt));
    const timer = setTimeout(() => {
      setLingered((all) => new Set([...all, ...waiting.filter((entry) => entry.dueAt <= dueAt).map((entry) => entry.id)]));
      refresh();
    }, Math.max(0, dueAt - Date.now()));
    return () => clearTimeout(timer);
  }, [states, seen, lingered, refresh]);

  // pending from the server, plus cards that left the list after something happened to them here;
  // each counts down from the list it last came in
  const cards = useMemo(() => {
    const listed = Object.values(byGrant(items, arrival));
    const live = new Set(listed.map(({ item }) => item.grant_id));
    const kept = Object.values(seen).filter(({ item }) => {
      if (live.has(item.grant_id)) return false;
      const state = states.get(item.grant_id);
      if (!state || state.phase === "elsewhere") return false;
      return state.phase !== "expired" || !lingered.has(item.grant_id);
    });
    return [...listed, ...kept].sort((a, b) => urgency(a.item, b.item));
  }, [items, arrival, seen, states, lingered]);

  const mine = cards.filter(({ item }) => item.mine);
  const others = cards.filter(({ item }) => !item.mine);
  const pendingCount = (items ?? []).length;

  const renderCard = ({ item, arrival: at }: Listed) => (
    <li key={item.grant_id}>
      <ApprovalItemCard item={item} arrival={at} userId={userId} />
    </li>
  );

  let body: React.ReactNode;
  if (items === null && error) {
    body = <ErrorState error={error} onRetry={refresh} />;
  } else if (items === null || arrival === null) {
    body = <LoadingCards />;
  } else if (cards.length === 0) {
    body = (
      <EmptyState
        icon={ShieldCheck}
        title={t("emptyTitle")}
        description={t("emptyBody")}
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link href="/hub">{t("goHub")}</Link>
            </Button>
            {canWrite ? (
              <Button asChild variant="outline" size="sm">
                <Link href="/import">{t("goImport")}</Link>
              </Button>
            ) : null}
          </div>
        }
      />
    );
  } else {
    body = (
      <div className="space-y-8">
        {mine.length > 0 ? (
          <Section title={t("mineTitle")} description={t("mineBody")}>
            <ul className="space-y-3">{mine.map(renderCard)}</ul>
          </Section>
        ) : null}
        {others.length > 0 ? (
          <Section title={t("othersTitle")} description={t("othersBody")}>
            <ul className="space-y-3">{others.map(renderCard)}</ul>
          </Section>
        ) : null}
      </div>
    );
  }

  return (
    <div className="w-full max-w-3xl">
      <PageHeader
        title={pages("title")}
        description={pages("description")}
        caption={items !== null && pendingCount > 0 ? t("pendingCount", { count: pendingCount }) : undefined}
        actions={
          <Button variant="outline" size="sm" onClick={refresh} aria-label={t("refresh")}>
            <RefreshCw aria-hidden="true" />
            <span className="hidden sm:inline">{t("refresh")}</span>
          </Button>
        }
      />
      {error && items !== null ? (
        <p role="alert" className="mb-3 rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning ring-1 ring-warning/30">
          {t("staleList")}
        </p>
      ) : null}
      {body}
    </div>
  );
}
