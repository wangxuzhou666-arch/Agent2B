import { render, type RenderOptions } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement, ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";

import en from "../messages/en.json";
import zh from "../messages/zh.json";

const MESSAGES = { en, zh } as const;

export type TestLocale = keyof typeof MESSAGES;

/**
 * The providers every console component expects (i18n, tooltips). Rendered directly, so a test
 * can switch the language mid-test: `rerender(<IntlProviders locale="zh">…</IntlProviders>)`.
 */
export function IntlProviders({ locale, children }: { locale: TestLocale; children: ReactNode }) {
  return (
    <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="UTC">
      <TooltipProvider>{children}</TooltipProvider>
    </NextIntlClientProvider>
  );
}

/** Render inside the providers every console component expects (i18n, tooltips). */
export function renderWithIntl(
  ui: ReactElement,
  { locale = "en", ...options }: { locale?: TestLocale } & Omit<RenderOptions, "wrapper"> = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return <IntlProviders locale={locale}>{children}</IntlProviders>;
  }
  return render(ui, { wrapper: Wrapper, ...options });
}
