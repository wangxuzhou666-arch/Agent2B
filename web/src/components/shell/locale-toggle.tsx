"use client";

import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useTransition } from "react";

import { Button } from "@/components/ui/button";
import { LOCALE_COOKIE, type Locale } from "@/i18n/config";

const ONE_YEAR_S = 60 * 60 * 24 * 365;

/** EN ⇄ 中文. The choice lives in a cookie the server reads; the URL never carries a locale. */
export function LocaleToggle() {
  const t = useTranslations("locale");
  const locale = useLocale();
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const next: Locale = locale === "en" ? "zh" : "en";

  const toggle = () => {
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=${ONE_YEAR_S}; samesite=lax`;
    startTransition(() => router.refresh());
  };

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={toggle}
      disabled={pending}
      aria-label={t("switchTo")}
      title={t("switchTo")}
      lang={next === "zh" ? "zh-CN" : "en"}
      className="min-w-10 px-2 text-sm font-medium text-muted-foreground"
    >
      {t("short")}
    </Button>
  );
}
