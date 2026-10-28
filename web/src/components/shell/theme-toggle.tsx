"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useMounted } from "@/hooks/use-mounted";

const OPTIONS = [
  { value: "light", icon: Sun },
  { value: "dark", icon: Moon },
  { value: "system", icon: Monitor },
] as const;

/** Light / dark / system (next-themes, class strategy). The icon shows the resolved theme. */
export function ThemeToggle() {
  const t = useTranslations("theme");
  const { theme, resolvedTheme, setTheme } = useTheme();
  const mounted = useMounted();
  const Icon = !mounted ? Monitor : resolvedTheme === "dark" ? Moon : Sun;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t("label")} className="text-muted-foreground">
          <Icon className="size-[18px] stroke-[1.75]" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuLabel className="text-caption text-muted-foreground">{t("label")}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={mounted ? (theme ?? "system") : "system"} onValueChange={setTheme}>
          {OPTIONS.map(({ value, icon: OptionIcon }) => (
            <DropdownMenuRadioItem key={value} value={value}>
              <OptionIcon className="size-4 stroke-[1.75]" />
              {t(value)}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
