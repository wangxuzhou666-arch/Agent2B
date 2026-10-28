"use client";

import { LogOut } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { initialOf } from "@/lib/format";

/**
 * Avatar initial, user id and role pill; the menu holds Sign out. The button is named by the
 * words it shows (user id, role) plus "Account", so its accessible name contains its visible
 * label (WCAG 2.5.3); the id and role still name it on narrow screens, where they are hidden.
 */
export function UserMenu() {
  const t = useTranslations();
  const roles = useTranslations("roles");
  const { session, signOut } = useAuth();
  const nameId = useId();
  const roleId = useId();
  const accountId = useId();
  if (!session) return null;
  const role = session.roles[0];
  const roleLabel = role ? roles(role) : null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-labelledby={roleLabel ? `${nameId} ${roleId} ${accountId}` : `${nameId} ${accountId}`}
          className="flex items-center gap-2 rounded-lg px-1.5 py-1 outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <span
            aria-hidden="true"
            className="flex size-8 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground"
          >
            {initialOf(session.userId)}
          </span>
          <span className="hidden min-w-0 flex-col items-start text-left sm:flex">
            <span id={nameId} className="max-w-40 truncate text-sm leading-tight font-semibold text-foreground">
              {session.userId}
            </span>
            {roleLabel ? (
              <span id={roleId} className="text-caption leading-tight text-muted-foreground">
                {roleLabel}
              </span>
            ) : null}
          </span>
          <span id={accountId} className="sr-only">
            {t("shell.account")}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <span className="block truncate text-sm font-medium">
            {t("shell.signedInAs", { user: session.userId })}
          </span>
          {roleLabel ? <span className="block text-caption text-muted-foreground">{roleLabel}</span> : null}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={signOut}>
          <LogOut className="stroke-[1.75]" />
          {t("shell.signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
