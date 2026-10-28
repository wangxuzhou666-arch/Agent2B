import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ApprovalsView } from "@/components/approvals/approvals-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("pages.approvals");
  return { title: t("title") };
}

export default function ApprovalsPage() {
  return <ApprovalsView />;
}
