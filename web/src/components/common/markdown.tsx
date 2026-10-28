import type { ReactNode } from "react";
import ReactMarkdown, { type Components, type UrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { QueryOut } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Who wrote the text: a language model (`model`), the backend's code in place of a model
 * (`template`), or the console's own sentences (`system`).
 */
export type NarrationSource = QueryOut["answer_source"];

/** Elements a narrated answer may use. Raw HTML is dropped, never rendered. */
const ALLOWED = [
  "p",
  "br",
  "strong",
  "em",
  "del",
  "ul",
  "ol",
  "li",
  "blockquote",
  "code",
  "pre",
  "hr",
  "h1",
  "h2",
  "h3",
  "h4",
  "a",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
];

const TABLE: Components["table"] = ({ children }) => (
  <div className="my-3 overflow-x-auto rounded-xl border border-border">
    <table className="my-0 rounded-none ring-0">{children}</table>
  </div>
);

const plainText = (children: ReactNode): string | null =>
  typeof children === "string"
    ? children
    : Array.isArray(children) && children.every((child) => typeof child === "string")
      ? children.join("")
      : null;

const URL_TEXT = "font-mono text-[0.85em] font-normal break-all text-muted-foreground";

/** "www.example.com" → "http://www.example.com", "a@b.example" → "mailto:a@b.example" (GFM autolinks). */
const withoutScheme = (url: string) => url.replace(/^(?:https?:\/\/|mailto:)/i, "");

/**
 * A link as text: its words, then the address it points to, so the reader sees where it goes and
 * nothing can be clicked (a prompt-injected link stays inert). A bare URL or address is shown once.
 */
const TEXT_LINK: Components["a"] = ({ href, children }) => {
  const url = (href ?? "").trim();
  if (!url) return <>{children}</>;
  const words = plainText(children)?.trim();
  if (words === "") return <span className={URL_TEXT}>{url}</span>;
  if (words === url || words === withoutScheme(url)) return <span className={URL_TEXT}>{words}</span>;
  return (
    <>
      {children} <span className={URL_TEXT}>{url}</span>
    </>
  );
};

const COMPONENTS: Components = { a: TEXT_LINK, table: TABLE };

/** Shown, never followed: the address exactly as it was written (inert text either way). */
const keepUrl: UrlTransform = (url) => url;

/**
 * Safe markdown for narrated answers: GFM, no raw HTML, and no live link whoever wrote the text.
 * A link renders as plain text that shows its URL. `template` text is no exception: it repeats a
 * tool's message and scope, and its fallbacks quote a result after a failed model call, so it can
 * carry stored data or a param a model proposed (`core/narration.py`, `core/agent_loop.py`).
 * `system` text can name stored values too, and no sentence of the console relies on a link, so
 * `source` does not change how a link renders.
 */
export function Markdown({
  children,
  className,
}: {
  children: string;
  /** Who wrote the text; links render as text for every source. */
  source: NarrationSource;
  className?: string;
}) {
  return (
    <div className={cn("report-prose", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        allowedElements={ALLOWED}
        unwrapDisallowed
        skipHtml
        components={COMPONENTS}
        urlTransform={keepUrl}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
