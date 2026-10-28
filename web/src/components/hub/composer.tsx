"use client";

import { ArrowUp, Square } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** QueryRequest.query is capped at 10 000 characters by the contract. */
const MAX_QUERY_CHARS = 10_000;
const MAX_HEIGHT_PX = 240;

export interface ComposerProps {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  /** `hero`: the large empty-state composer. `dock`: sticky under the transcript. */
  variant?: "hero" | "dock";
  autoFocus?: boolean;
  className?: string;
}

/**
 * Enter sends, Shift+Enter adds a line, an IME composition (e.g. Chinese input) never sends.
 * While a query runs the send button turns into Stop; typing the next question stays possible.
 */
export function Composer({ busy, onSend, onStop, variant = "dock", autoFocus = false, className }: ComposerProps) {
  const t = useTranslations("hub");
  const id = useId();
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = !busy && text.trim().length > 0;

  // Grow with the content up to a cap, then scroll (works where `field-sizing` does not).
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  const submit = () => {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    // keyCode 229: the Enter that confirms an IME candidate in browsers without isComposing
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    submit();
  };

  const hero = variant === "hero";
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      data-active={busy ? "" : undefined}
      className={cn(
        "ai-glow rounded-2xl bg-card ring-1 ring-foreground/10",
        hero ? "p-4 shadow-sm" : "px-4 py-3 shadow-lg shadow-foreground/5",
        className,
      )}
    >
      <label htmlFor={id} className="sr-only">
        {t("composerLabel")}
      </label>
      <textarea
        id={id}
        ref={ref}
        data-slot="composer-input"
        rows={hero ? 2 : 1}
        value={text}
        maxLength={MAX_QUERY_CHARS}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={t("placeholder")}
        autoFocus={autoFocus}
        className="block w-full resize-none bg-transparent px-1 pt-1 pb-3 text-answer leading-relaxed text-foreground outline-none placeholder:text-faint"
      />
      <div className="flex items-center justify-end gap-2">
        {busy ? (
          <Button
            type="button"
            variant="ink"
            size={hero ? "icon-lg" : "icon"}
            onClick={onStop}
            aria-label={t("stop")}
            title={t("stop")}
          >
            <Square className="size-4 fill-current" />
          </Button>
        ) : (
          <Button
            type="submit"
            variant="ink"
            size={hero ? "icon-lg" : "icon"}
            disabled={!canSend}
            aria-label={t("send")}
            title={t("send")}
          >
            <ArrowUp className="size-4 stroke-2" />
          </Button>
        )}
      </div>
    </form>
  );
}
