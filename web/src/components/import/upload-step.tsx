"use client";

import { FileUp, LoaderCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { MAX_UPLOAD_BYTES, UPLOAD_EXTENSIONS } from "@/lib/config";
import type { ImportTarget } from "@/lib/entities";
import type { UploadIssue } from "@/lib/imports";
import { cn } from "@/lib/utils";

import { UploadIssueNotice } from "./upload-issue";

const ACCEPT = [
  ...UPLOAD_EXTENSIONS,
  "text/csv",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
].join(",");

/** The columns a file for this target may carry, from the spec (required ones marked). */
function ExpectedColumns({ target }: { target: ImportTarget }) {
  const t = useTranslations("import.upload");
  const fields = [...target.view.fields, ...target.view.attrs];
  return (
    <div className="space-y-1.5">
      <p className="text-caption text-muted-foreground">{t("columns")}</p>
      <ul className="flex flex-wrap gap-1.5">
        {fields.map((field) => {
          const required = field.required || field.id === target.view.root.primary_key;
          return (
            <li
              key={field.id}
              title={field.aliases.length > 0 ? t("aliases", { aliases: field.aliases.join(", ") }) : undefined}
              className={cn(
                "inline-flex h-6 items-center gap-1 rounded-full border px-2.5 text-xs",
                required ? "border-border bg-secondary text-foreground" : "border-border/70 text-muted-foreground",
              )}
            >
              {field.label}
              {required ? (
                <>
                  <span aria-hidden="true" className="text-destructive">
                    *
                  </span>
                  <span className="sr-only">({t("required")})</span>
                </>
              ) : null}
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-faint">{t("matching")}</p>
    </div>
  );
}

/**
 * Step 1: where the rows go and which file. Drag-and-drop or the file picker; the browser
 * checks type and size first, the server decides.
 */
export function UploadStep({
  targets,
  entityId,
  busy,
  issue,
  notice,
  onSelectEntity,
  onFile,
}: {
  targets: readonly ImportTarget[];
  entityId: string | null;
  busy: boolean;
  issue: UploadIssue | null;
  notice: "rejected" | "expired" | null;
  onSelectEntity: (id: string) => void;
  onFile: (file: File) => void;
}) {
  const t = useTranslations("import.upload");
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const target = targets.find((x) => x.id === entityId) ?? targets[0] ?? null;
  const limitMb = Math.round((MAX_UPLOAD_BYTES / (1024 * 1024)) * 10) / 10;

  const zone = useRef<HTMLDivElement>(null);
  const take = (files: FileList | null | undefined) => {
    const file = files?.[0];
    if (file && !busy) onFile(file);
  };
  const takeRef = useRef(take);
  useEffect(() => {
    takeRef.current = take;
  });

  // Dropping is a pointer gesture layered on the drop zone; the "Choose file" button is the
  // keyboard path, so the zone itself stays a plain, non-interactive element.
  useEffect(() => {
    const el = zone.current;
    if (!el) return;
    const over = (event: DragEvent) => {
      event.preventDefault();
      if (!busy) setDragging(true);
    };
    const leave = (event: DragEvent) => {
      if (event.relatedTarget instanceof Node && el.contains(event.relatedTarget)) return;
      setDragging(false);
    };
    const drop = (event: DragEvent) => {
      event.preventDefault();
      setDragging(false);
      takeRef.current(event.dataTransfer?.files);
    };
    el.addEventListener("dragenter", over);
    el.addEventListener("dragover", over);
    el.addEventListener("dragleave", leave);
    el.addEventListener("drop", drop);
    return () => {
      el.removeEventListener("dragenter", over);
      el.removeEventListener("dragover", over);
      el.removeEventListener("dragleave", leave);
      el.removeEventListener("drop", drop);
    };
  }, [busy]);

  return (
    <div className="space-y-5">
      {notice ? (
        <p role="status" className="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
          {t(notice === "rejected" ? "noticeRejected" : "noticeExpired")}
        </p>
      ) : null}
      <div className="grid gap-1.5">
        <label htmlFor={`${id}-target`} className="text-sm font-medium text-foreground">
          {t("target")}
        </label>
        <NativeSelect
          id={`${id}-target`}
          value={target?.id ?? ""}
          disabled={busy}
          onChange={(e) => onSelectEntity(e.target.value)}
          className="w-full sm:w-80"
        >
          {targets.map((x) => (
            <option key={x.id} value={x.id}>
              {x.path}
            </option>
          ))}
        </NativeSelect>
        {target?.view.description ? <p className="text-xs text-muted-foreground">{target.view.description}</p> : null}
      </div>

      {target ? <ExpectedColumns target={target} /> : null}

      <div
        ref={zone}
        data-testid="import-dropzone"
        data-dragging={dragging || undefined}
        className={cn(
          "flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors",
          dragging ? "border-primary bg-accent/50" : "border-border bg-background",
          busy && "opacity-70",
        )}
      >
        {busy ? (
          <>
            <LoaderCircle aria-hidden="true" className="size-6 animate-spin text-muted-foreground" />
            <p role="status" className="text-xs text-muted-foreground">
              {t("parsing")}
            </p>
          </>
        ) : (
          <>
            <FileUp aria-hidden="true" className="size-6 stroke-[1.75] text-faint" />
            <p className="text-sm font-medium text-foreground">{t("drop")}</p>
            <p className="text-xs text-faint">{t("or")}</p>
            <Button type="button" variant="outline" size="sm" disabled={!target} onClick={() => input.current?.click()}>
              {t("choose")}
            </Button>
            <p className="mt-1 text-xs text-faint">{t("limits", { size: limitMb })}</p>
          </>
        )}
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          aria-label={t("choose")}
          tabIndex={-1}
          className="sr-only"
          onChange={(e) => {
            take(e.target.files);
            e.target.value = ""; // picking the same file again still fires
          }}
        />
      </div>

      {issue ? <UploadIssueNotice issue={issue} /> : null}
    </div>
  );
}
