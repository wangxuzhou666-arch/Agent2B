import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { PageHeader } from "@/components/common/page-header";
import { ImportWizard } from "@/components/import/import-wizard";
import { RequireRole } from "@/components/shell/require-role";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("pages.import");
  return { title: t("title") };
}

export default async function ImportPage() {
  const t = await getTranslations("pages.import");
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      <RequireRole roles={["operator"]}>
        <Suspense fallback={null}>
          <ImportWizard />
        </Suspense>
      </RequireRole>
    </>
  );
}
