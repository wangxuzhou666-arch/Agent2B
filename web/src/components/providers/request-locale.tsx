"use client";

import { useLocale } from "next-intl";
import { useLayoutEffect } from "react";

import { setRequestLocale } from "@/lib/api";

/**
 * Keeps the API client's `Accept-Language` equal to the active locale. A layout effect, so it is
 * set before any passive effect fetches: the first requests after a load or a language switch
 * already ask for the new language, and views that refetch on the switch get it.
 */
export function RequestLocale() {
  const locale = useLocale();
  useLayoutEffect(() => setRequestLocale(locale), [locale]);
  return null;
}
