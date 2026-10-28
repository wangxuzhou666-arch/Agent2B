import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { buttonVariants } from "@/components/ui/button";

export default async function NotFound() {
  const t = await getTranslations();
  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="font-serif text-2xl font-semibold tracking-tight">{t("errors.notFound")}</h1>
      <Link href="/hub" className={buttonVariants({ variant: "outline", size: "sm" })}>
        {t("shell.hub")}
      </Link>
    </main>
  );
}
