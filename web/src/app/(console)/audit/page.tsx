import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { AuditView } from "@/components/audit/audit-view";
import { PageHeader } from "@/components/common/page-header";
import { RequireRole } from "@/components/shell/require-role";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("pages.audit");
  return { title: t("title") };
}

export default async function AuditPage() {
  const t = await getTranslations("pages.audit");
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      <RequireRole roles={["operator"]}>
        {/* the view reads its filters from the URL (useSearchParams needs a boundary) */}
        <Suspense fallback={null}>
          <AuditView />
        </Suspense>
      </RequireRole>
    </>
  );
}
