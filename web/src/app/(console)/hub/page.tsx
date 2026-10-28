import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { HubView } from "@/components/hub/hub-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("hub");
  return { title: t("title") };
}

export default function HubPage() {
  return <HubView />;
}
