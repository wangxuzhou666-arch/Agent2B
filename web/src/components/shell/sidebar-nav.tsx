"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { useAuth } from "@/components/providers/auth-provider";
import { useApprovals } from "@/components/providers/approvals-provider";
import { hasRole } from "@/lib/session";
import { cn } from "@/lib/utils";

import { isActive, NAV_GROUPS, type NavItem } from "./nav-items";

function PendingCount({ count, collapsed }: { count: number; collapsed: boolean }) {
  const t = useTranslations("shell");
  if (count <= 0) return null;
  const label = <span className="sr-only">, {t("pendingApprovals", { count })}</span>;
  if (collapsed) {
    return (
      <>
        <span
          aria-hidden="true"
          className="absolute top-1.5 right-1.5 size-2 rounded-full bg-warning ring-2 ring-sidebar"
        />
        {label}
      </>
    );
  }
  return (
    <>
      <span
        aria-hidden="true"
        className="ml-auto inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-warning/10 px-1.5 text-xs font-medium text-warning tabular-nums dark:bg-warning/20"
      >
        {count}
      </span>
      {label}
    </>
  );
}

function NavLink({
  item,
  collapsed,
  onNavigate,
}: {
  item: NavItem;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const t = useTranslations("shell");
  const pathname = usePathname();
  const { count } = useApprovals();
  const active = isActive(pathname, item.href);
  const Icon = item.icon;
  const label = t(item.key);
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      title={collapsed ? label : undefined}
      data-touch-target
      className={cn(
        "relative flex items-center rounded-md text-nav outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50",
        collapsed ? "mx-auto size-10 justify-center" : "gap-3.5 px-3 py-2.5",
        active
          ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground"
          : "text-foreground/80 hover:bg-sidebar-accent/60 hover:text-foreground",
      )}
    >
      <Icon aria-hidden="true" className="size-[18px] shrink-0 stroke-[1.75]" />
      {collapsed ? <span className="sr-only">{label}</span> : <span className="truncate">{label}</span>}
      {item.key === "approvals" ? <PendingCount count={count} collapsed={collapsed} /> : null}
    </Link>
  );
}

/** The console navigation, grouped (DESIGN 10.1). Role-gated items are hidden, not disabled. */
export function SidebarNav({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const t = useTranslations("shell");
  const { session } = useAuth();
  return (
    <nav
      aria-label={t("primaryNav")}
      className={cn("flex-1 overflow-y-auto py-4", collapsed ? "space-y-1 px-2" : "space-y-5 px-3")}
    >
      {NAV_GROUPS.map((group) => {
        const items = group.items.filter((item) => !item.roles || hasRole(session?.roles, ...item.roles));
        if (items.length === 0) return null;
        return (
          <div key={group.label} role="group" aria-label={t(group.label)}>
            {collapsed ? null : (
              <p className="mb-1.5 px-3 text-nav font-medium text-muted-foreground">{t(group.label)}</p>
            )}
            <ul className="space-y-0.5">
              {items.map((item) => (
                <li key={item.key}>
                  <NavLink item={item} collapsed={collapsed} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}
