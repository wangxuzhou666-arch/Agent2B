import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { DataView } from "@/components/data/data-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("pages.data");
  return { title: t("title") };
}

export default function DataPage() {
  return (
    // the view keeps entity, tab, sort, filters and page in the URL (useSearchParams needs a boundary)
    <Suspense fallback={null}>
      <DataView />
    </Suspense>
  );
}
