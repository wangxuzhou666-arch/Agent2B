import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { AuthProvider } from "@/components/providers/auth-provider";
import { RequestLocale } from "@/components/providers/request-locale";
import { ThemeProvider } from "@/components/providers/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { htmlLang } from "@/i18n/config";

import "./globals.css";

// The console's one web font. Its files come from an npm package, so `next build` fetches nothing
// and works offline or where a font host is unreachable. Latin subset only: the serif sets
// English titles, and Chinese text uses system fonts (globals.css). The fallback face shown until
// it loads is metric-matched Times New Roman, a serif like the font itself.
const baskerville = localFont({
  src: [
    {
      path: "../../node_modules/@fontsource/libre-baskerville/files/libre-baskerville-latin-400-normal.woff2",
      weight: "400",
      style: "normal",
    },
    {
      path: "../../node_modules/@fontsource/libre-baskerville/files/libre-baskerville-latin-700-normal.woff2",
      weight: "700",
      style: "normal",
    },
  ],
  variable: "--font-libre-baskerville",
  display: "swap",
  adjustFontFallback: "Times New Roman",
});

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");
  return {
    title: { default: t("title"), template: `%s · ${t("title")}` },
    description: t("description"),
    robots: { index: false, follow: false },
  };
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafa" },
    { media: "(prefers-color-scheme: dark)", color: "#1a1a1a" },
  ],
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  const messages = await getMessages();
  return (
    <html
      lang={htmlLang[locale]}
      suppressHydrationWarning
      className={baskerville.variable}
    >
      <body>
        <NextIntlClientProvider locale={locale} messages={messages}>
          {/* its layout effect sets the API's Accept-Language before any view's fetch effect runs */}
          <RequestLocale />
          <ThemeProvider>
            <TooltipProvider delayDuration={300}>
              <AuthProvider>{children}</AuthProvider>
              <Toaster position="bottom-right" />
            </TooltipProvider>
          </ThemeProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
