"use client";

import { Menu, PanelLeft, SquarePen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

import { useConversation } from "@/components/hub/conversation-provider";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useMediaQuery } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";

import { DemoBanner } from "./demo-banner";
import { LocaleToggle } from "./locale-toggle";
import { ConsoleModelIndicator } from "./model-indicator";
import { isActive } from "./nav-items";
import { ProductMark, Wordmark } from "./product-mark";
import { SidebarNav } from "./sidebar-nav";
import { ThemeToggle } from "./theme-toggle";
import { UserMenu } from "./user-menu";

function NewConversationButton() {
  const t = useTranslations("hub");
  const { turns, reset } = useConversation();
  if (turns.length === 0) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-lg"
          onClick={reset}
          aria-label={t("newConversation")}
          className="size-10 rounded-lg hover:bg-accent"
        >
          <SquarePen className="size-5 stroke-[2.25]" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{t("newConversation")}</TooltipContent>
    </Tooltip>
  );
}

function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const t = useTranslations("shell");
  const pathname = usePathname();
  const onHub = isActive(pathname, "/hub");
  return (
    <div className="flex h-14 items-center gap-3 bg-background/90 px-4 backdrop-blur md:px-8">
      <Button
        variant="ghost"
        size="icon"
        onClick={onOpenNav}
        aria-label={t("openNav")}
        className="text-muted-foreground md:hidden"
      >
        <Menu className="size-5 stroke-[1.75]" />
      </Button>
      {onHub ? (
        <span className="text-page-label font-semibold tracking-wide text-foreground">{t("hub")}</span>
      ) : null}
      <div className="ml-auto flex items-center gap-1">
        <ConsoleModelIndicator className="mr-1" />
        {onHub ? <NewConversationButton /> : null}
        <LocaleToggle />
        <ThemeToggle />
        <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />
        <UserMenu />
      </div>
    </div>
  );
}

/**
 * Console frame (DESIGN 10.1): fixed sidebar (240 px, 64 px rail when collapsed, a sheet below
 * md), sticky header with the demo banner and top bar, and the content column.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const t = useTranslations("shell");
  const common = useTranslations("common");
  const { session } = useAuth();
  const wide = useMediaQuery("(min-width: 64rem)", true);
  const [userCollapsed, setUserCollapsed] = useState<boolean | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const collapsed = userCollapsed ?? !wide;

  return (
    <div className="min-h-svh bg-background">
      <a
        href="#main"
        className="sr-only z-overlay rounded-md bg-background px-3 py-2 text-sm font-medium focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:ring-3 focus:ring-ring/50"
      >
        {t("skipToContent")}
      </a>

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-sidebar hidden flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-200 md:flex",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <div className={cn("flex items-center py-4", collapsed ? "justify-center px-2" : "gap-2.5 px-4")}>
          {collapsed ? (
            <button
              type="button"
              onClick={() => setUserCollapsed(false)}
              aria-label={t("expand")}
              title={t("expand")}
              className="flex size-10 items-center justify-center rounded-md outline-none hover:bg-sidebar-accent/60 focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <ProductMark />
            </button>
          ) : (
            <>
              <Link href="/hub" className="flex items-center gap-2.5 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
                <ProductMark />
                <Wordmark />
              </Link>
              <button
                type="button"
                onClick={() => setUserCollapsed(true)}
                aria-label={t("collapse")}
                title={t("collapse")}
                className="ml-auto flex size-8 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-sidebar-accent/60 hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <PanelLeft className="size-[18px] stroke-[1.75]" />
              </button>
            </>
          )}
        </div>
        <SidebarNav collapsed={collapsed} />
      </aside>

      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent side="left" closeLabel={common("close")} className="w-[min(22rem,100vw)] gap-0 bg-sidebar p-0">
          <SheetTitle className="flex items-center gap-2.5 px-4 py-4">
            <ProductMark />
            <Wordmark />
          </SheetTitle>
          <SheetDescription className="sr-only">{t("primaryNav")}</SheetDescription>
          <SidebarNav onNavigate={() => setNavOpen(false)} />
        </SheetContent>
      </Sheet>

      <div
        className={cn(
          "flex min-h-svh flex-col transition-[padding] duration-200",
          collapsed ? "md:pl-16" : "md:pl-60",
        )}
      >
        <header className="sticky top-0 z-header">
          {session?.demoMode ? <DemoBanner /> : null}
          <TopBar onOpenNav={() => setNavOpen(true)} />
        </header>
        <main id="main" tabIndex={-1} className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col px-4 pt-6 pb-8 outline-none md:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
