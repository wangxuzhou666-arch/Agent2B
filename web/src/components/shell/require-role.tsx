"use client";

import { Lock } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { useAuth } from "@/components/providers/auth-provider";
import type { Role } from "@/lib/api";
import { hasRole } from "@/lib/session";

/** Renders `children` only for the listed roles (admin always passes); others see "No access". */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const t = useTranslations("auth");
  const { session } = useAuth();
  if (hasRole(session?.roles, ...roles)) return <>{children}</>;
  return <EmptyState icon={Lock} title={t("forbiddenTitle")} description={t("forbiddenBody")} />;
}
