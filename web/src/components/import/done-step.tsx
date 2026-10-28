"use client";

import { Check } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { htmlLang } from "@/i18n/config";
import type { ImportResultOut } from "@/lib/api";
import type { ImportTarget } from "@/lib/entities";
import { formatNumber } from "@/lib/format";
import type { UploadedFile } from "@/lib/imports";

/** Step 4 (DESIGN 10.9 result): what was written, what was skipped, and where to look next. */
export function DoneStep({
  target,
  file,
  result,
  onAgain,
}: {
  target: ImportTarget | null;
  file: UploadedFile;
  result: ImportResultOut;
  onAgain: () => void;
}) {
  const t = useTranslations("import.done");
  const locale = htmlLang[useLocale()];
  const dataHref = `/data?entity=${encodeURIComponent(result.entity)}` as Route;
  const auditHref = `/audit?id=${result.audit_id}` as Route;
  return (
    <div className="space-y-4">
      <p role="status" className="flex items-center gap-1.5 text-nav font-medium text-success">
        <Check aria-hidden="true" className="size-4 stroke-[2.5]" />
        {t("written", { count: result.written, formatted: formatNumber(result.written, locale), entity: target?.path ?? result.entity })}
      </p>
      {result.skipped > 0 ? (
        <p className="text-xs text-warning">{t("skipped", { count: result.skipped, formatted: formatNumber(result.skipped, locale) })}</p>
      ) : null}
      <p className="text-xs text-faint">
        {t("meta", { file: file.name, id: result.audit_id })} ·{" "}
        <span className="font-mono" title={result.digest}>
          {result.digest.slice(0, 12)}
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button asChild variant="outline" size="sm">
          <Link href={dataHref}>{t("viewData")}</Link>
        </Button>
        <Button asChild variant="outline" size="sm">
          <Link href={auditHref}>{t("viewAudit")}</Link>
        </Button>
        <Button variant="ink" size="sm" onClick={onAgain}>
          {t("again")}
        </Button>
      </div>
    </div>
  );
}
