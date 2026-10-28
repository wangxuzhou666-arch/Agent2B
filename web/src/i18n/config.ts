export const locales = ["en", "zh"] as const;

export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "en";

/** Cookie the locale toggle writes and the request config reads (no locale in the URL). */
export const LOCALE_COOKIE = "NEXT_LOCALE";

/** BCP 47 tag for `<html lang>`: drives CJK glyph selection and line breaking. */
export const htmlLang: Record<Locale, string> = { en: "en", zh: "zh-CN" };

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (locales as readonly string[]).includes(value);
}
