"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef } from "react";

import { useApprovalState } from "@/components/approval/use-approval";

import { Composer } from "./composer";
import { useConversation } from "./conversation-provider";
import { TurnView } from "./turn-view";

const STARTERS = ["late", "lowStock", "suppliers", "createPo"] as const;

function StarterQueries({ onPick, disabled }: { onPick: (query: string) => void; disabled: boolean }) {
  const t = useTranslations("hub");
  const starters = useTranslations("hub.starters");
  return (
    <div className="mt-5">
      <p className="mb-2 text-center text-caption text-faint">{t("startersLabel")}</p>
      <ul className="flex flex-wrap justify-center gap-2">
        {STARTERS.map((key) => {
          const query = starters(key);
          return (
            <li key={key}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onPick(query)}
                data-touch-target
                className="rounded-full border border-border bg-card px-3.5 py-1.5 text-sm text-muted-foreground transition-colors outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-40"
              >
                {query}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Keep the newest turn in view while the reader is at the bottom; leave them be once they
 * scroll up. Programmatic scrolls are instant, so an animation never reads as "scrolled up".
 */
function useFollowBottom(signal: unknown, forceSignal: unknown) {
  const atBottom = useRef(true);

  useEffect(() => {
    const onScroll = () => {
      const el = document.documentElement;
      atBottom.current = window.innerHeight + window.scrollY >= el.scrollHeight - 120;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    atBottom.current = true; // a new question always scrolls into view
  }, [forceSignal]);

  useEffect(() => {
    if (atBottom.current) window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "auto" });
  }, [signal, forceSignal]);
}

/** Hub: the conversation with the engine. */
export function HubView() {
  const t = useTranslations("hub");
  const { turns, busy, send, stop } = useConversation();
  const last = turns.at(-1);
  const lastApproval = useApprovalState(last?.response?.approval?.grant_id ?? null);
  useFollowBottom(
    `${turns.length}:${last?.steps.length ?? 0}:${last?.status ?? ""}:${lastApproval.phase}`,
    turns.length,
  );

  if (turns.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center pt-10 pb-24">
        <h1 className="mb-10 text-center font-serif text-4xl font-medium tracking-tight text-foreground">
          {t("greeting")}
        </h1>
        <div className="w-full max-w-3xl">
          <Composer variant="hero" busy={busy} onSend={(q) => send(q)} onStop={stop} autoFocus />
          <StarterQueries onPick={(q) => send(q)} disabled={busy} />
          <p className="pt-6 text-center text-caption text-faint">{t("footnote")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="@container/hub -mb-8 flex flex-1 flex-col">
      <h1 className="sr-only">{t("title")}</h1>
      <div
        role="log"
        aria-live="polite"
        aria-label={t("transcript")}
        className="mx-auto w-full max-w-3xl flex-1 space-y-8 pt-2 pb-6"
      >
        {turns.map((turn, index) => (
          <TurnView key={turn.id} turn={turn} latest={index === turns.length - 1} busy={busy} />
        ))}
      </div>
      {/* full width, above the transcript, so wide tables scroll under it rather than over it */}
      <div className="sticky bottom-0 z-header w-full bg-linear-to-t from-background via-background/95 to-transparent pt-6 pb-1">
        <div className="mx-auto w-full max-w-3xl">
          <Composer busy={busy} onSend={(q) => send(q)} onStop={stop} />
          <p className="pt-2 pb-2 text-center text-caption text-faint">{busy ? t("footnoteBusy") : t("footnote")}</p>
        </div>
      </div>
    </div>
  );
}
