"use client";

import { useTranslations } from "next-intl";

import { MAX_UPLOAD_BYTES } from "@/lib/config";
import type { UploadIssue } from "@/lib/imports";
import { cn } from "@/lib/utils";

const MEGABYTE = 1024 * 1024;

/** Upload / commit failure as a destructive notice: our sentence first, the server's detail after. */
export function UploadIssueNotice({ issue, className }: { issue: UploadIssue; className?: string }) {
  const t = useTranslations("import.issue");
  const limit = `${Math.round((MAX_UPLOAD_BYTES / MEGABYTE) * 10) / 10} MB`;
  return (
    <div
      role="alert"
      data-issue={issue.key}
      className={cn("rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive ring-1 ring-destructive/30", className)}
    >
      <p className="font-medium">{t(issue.key, { limit })}</p>
      {issue.detail ? <p className="mt-0.5 break-words opacity-90">{issue.detail}</p> : null}
    </div>
  );
}
