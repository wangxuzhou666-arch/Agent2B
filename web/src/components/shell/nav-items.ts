import { Database, MessagesSquare, ScrollText, ShieldCheck, Upload, type LucideIcon } from "lucide-react";
import type { Route } from "next";

import type { Role } from "@/lib/api";

export type NavKey = "hub" | "approvals" | "data" | "import" | "audit";

export interface NavItem {
  key: NavKey;
  href: Route;
  icon: LucideIcon;
  /** Roles that may see the item (admin always can). Omit for everyone. */
  roles?: Role[];
}

export interface NavGroup {
  label: "groupAgent" | "groupData" | "groupGovernance";
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "groupAgent",
    items: [
      { key: "hub", href: "/hub", icon: MessagesSquare },
      { key: "approvals", href: "/approvals", icon: ShieldCheck },
    ],
  },
  {
    label: "groupData",
    items: [
      { key: "data", href: "/data", icon: Database },
      { key: "import", href: "/import", icon: Upload, roles: ["operator"] },
    ],
  },
  {
    label: "groupGovernance",
    items: [{ key: "audit", href: "/audit", icon: ScrollText, roles: ["operator"] }],
  },
];

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
