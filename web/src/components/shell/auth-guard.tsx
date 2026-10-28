"use client";

import type { Route } from "next";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";

function Centered({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return (
    <div
      role="status"
      aria-busy={busy}
      aria-live="polite"
      className="flex min-h-svh flex-col items-center justify-center gap-3 px-6 text-center text-sm text-muted-foreground"
    >
      {children}
    </div>
  );
}

/**
 * Console routes need a session. Signed out → /login?next=<here>, where "here" keeps the query,
 * so a deep link (an audit entry, a filtered Data view) opens after sign-in. API unreachable
 * while a session exists → keep it and offer Retry (DESIGN 10.1).
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const t = useTranslations();
  const { status, revalidate } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "signed-out") {
      // Not the hash: no window keeps state there, and Next 16.4 repeats it ("#a#a") when a
      // client navigation returns to the URL the document was first loaded at.
      const here = `${pathname}${window.location.search}`;
      router.replace(`/login?next=${encodeURIComponent(here)}` as Route);
    }
  }, [status, router, pathname]);

  if (status === "signed-in") return <>{children}</>;
  if (status === "offline") {
    return (
      <Centered>
        <p className="max-w-sm">{t("auth.offline")}</p>
        <Button variant="outline" size="sm" onClick={revalidate}>
          {t("common.retry")}
        </Button>
      </Centered>
    );
  }
  return (
    <Centered busy>
      <p>{t("auth.checking")}</p>
    </Centered>
  );
}
