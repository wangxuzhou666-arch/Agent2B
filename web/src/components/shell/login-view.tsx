"use client";

import { ArrowRight, LoaderCircle } from "lucide-react";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useId, useState, type FormEvent } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, type HealthOut, type Role } from "@/lib/api";
import { API_BASE } from "@/lib/config";

import { DemoBanner } from "./demo-banner";
import { LocaleToggle } from "./locale-toggle";
import { ModelIndicator } from "./model-indicator";
import { ProductMark, Wordmark } from "./product-mark";
import { ThemeToggle } from "./theme-toggle";

const DEMO_ROLES: Role[] = ["admin", "operator", "viewer"];

const AFTER_SIGN_IN: Route = "/hub";

/**
 * C0 controls and DEL. The URL parser drops TAB, CR and LF wherever they are, so
 * `/\t/evil.example` reaches it as `//evil.example`: another host.
 */
const CONTROL_CHARACTER = /[\u0000-\u001F\u007F]/;

/**
 * Where to go after sign-in (no open redirect): `raw` resolved against `origin` the way the
 * browser would resolve it, kept only when it stays on that origin. Control characters and
 * backslashes are refused outright; what is left comes back as path + query + hash, so a deep
 * link survives. Anything else, and /login itself, falls back to /hub.
 */
export function safeNext(raw: string | null, origin: string): Route {
  if (!raw || CONTROL_CHARACTER.test(raw) || raw.includes("\\")) return AFTER_SIGN_IN;
  let base: URL;
  let url: URL;
  try {
    base = new URL(origin);
    url = new URL(raw, base);
  } catch {
    return AFTER_SIGN_IN;
  }
  if (url.origin !== base.origin) return AFTER_SIGN_IN;
  const path = `${url.pathname}${url.search}${url.hash}`;
  // "/.//evil.example" parses to the path "//evil.example": protocol-relative once on its own
  if (path.startsWith("//") || new URL(path, base).origin !== base.origin) return AFTER_SIGN_IN;
  if (url.pathname === "/login" || url.pathname.startsWith("/login/")) return AFTER_SIGN_IN;
  return path as Route;
}

type Health = { state: "loading" } | { state: "ready"; health: HealthOut } | { state: "error" };

function useSignInError(): (error: unknown) => string {
  const t = useTranslations("login");
  return (error) => {
    if (error instanceof ApiError) {
      if (error.kind === "network") return t("unreachable", { url: API_BASE });
      if (error.status === 401) return t("invalidCredentials");
      if (error.status === 429) return t("tooManyAttempts");
      return t("failed", { message: error.message });
    }
    return t("failed", { message: String(error) });
  };
}

function DemoRoles() {
  const t = useTranslations();
  const roles = useTranslations("roles");
  const summaries = useTranslations("roleSummary");
  const { signInDemo } = useAuth();
  const describe = useSignInError();
  const [pending, setPending] = useState<Role | null>(null);
  const [error, setError] = useState<string | null>(null);

  const choose = async (role: Role) => {
    setPending(role);
    setError(null);
    try {
      await signInDemo(role); // the page navigates once the session is live
    } catch (err) {
      setError(describe(err));
      setPending(null);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-base font-semibold text-foreground">{t("login.demoTitle")}</h2>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{t("login.demoHint")}</p>
      </div>
      <ul className="space-y-2">
        {DEMO_ROLES.map((role) => (
          <li key={role}>
            <button
              type="button"
              onClick={() => void choose(role)}
              disabled={pending !== null}
              aria-label={t("login.signInAs", { role: roles(role) })}
              className="group flex w-full items-center gap-3 rounded-lg bg-card px-4 py-3 text-left ring-1 ring-foreground/10 transition-colors outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-foreground">
                  {t("login.signInAs", { role: roles(role) })}
                </span>
                <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                  {summaries(role)}
                </span>
              </span>
              {pending === role ? (
                <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin text-muted-foreground" />
              ) : (
                <ArrowRight
                  aria-hidden="true"
                  className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
                />
              )}
            </button>
          </li>
        ))}
      </ul>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function PasswordForm() {
  const t = useTranslations("login");
  const { signIn } = useAuth();
  const describe = useSignInError();
  const userId = useId();
  const passId = useId();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username || !password) return;
    setPending(true);
    setError(null);
    try {
      await signIn(username, password); // the page navigates once the session is live
    } catch (err) {
      setError(describe(err));
      setPassword("");
      setPending(false);
    }
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-4" noValidate>
      <div className="space-y-1.5">
        <Label htmlFor={userId}>{t("username")}</Label>
        <Input
          id={userId}
          autoComplete="username"
          value={username}
          maxLength={64}
          onChange={(e) => setUsername(e.target.value)}
          aria-invalid={error ? true : undefined}
          required
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={passId}>{t("password")}</Label>
        <Input
          id={passId}
          type="password"
          autoComplete="current-password"
          value={password}
          maxLength={256}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={error ? true : undefined}
          required
        />
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button type="submit" className="w-full" size="lg" disabled={pending || !username || !password}>
        {pending ? <LoaderCircle aria-hidden="true" className="animate-spin" /> : null}
        {pending ? t("signingIn") : t("submit")}
      </Button>
    </form>
  );
}

/** /login: demo role cards when the server runs in demo mode, a password form otherwise. */
export function LoginView() {
  const t = useTranslations();
  const router = useRouter();
  const rawNext = useSearchParams().get("next");
  const { status } = useAuth();
  const [health, setHealth] = useState<Health>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    // resolved here, in the browser: the origin it is checked against is the page's own
    if (status === "signed-in") router.replace(safeNext(rawNext, window.location.origin));
  }, [status, router, rawNext]);

  useEffect(() => {
    const controller = new AbortController();
    api
      .health(controller.signal)
      .then((h) => setHealth({ state: "ready", health: h }))
      .catch(() => {
        if (!controller.signal.aborted) setHealth({ state: "error" });
      });
    return () => controller.abort();
  }, [attempt]);

  const retry = () => {
    setHealth({ state: "loading" });
    setAttempt((n) => n + 1);
  };

  return (
    <div className="flex min-h-svh flex-col bg-background">
      {health.state === "ready" && health.health.demo_mode ? <DemoBanner /> : null}
      <div className="flex items-center justify-end gap-1 px-4 py-3">
        <ModelIndicator provider={health.state === "ready" ? health.health.provider : null} className="mr-1" />
        <LocaleToggle />
        <ThemeToggle />
      </div>
      <main className="flex flex-1 items-start justify-center px-4 pt-[8vh] pb-16">
        <div className="w-full max-w-md">
          <div className="mb-8 flex flex-col items-center text-center">
            <div className="flex items-center gap-2.5">
              <ProductMark />
              <Wordmark className="text-2xl" />
            </div>
            <p className="mt-3 text-nav leading-relaxed text-muted-foreground">{t("login.tagline")}</p>
          </div>
          <section
            aria-labelledby="login-title"
            className="rounded-xl bg-card p-6 shadow-sm ring-1 ring-foreground/10"
          >
            <h1 id="login-title" className="mb-5 font-serif text-2xl leading-tight font-semibold tracking-tight">
              {t("login.title")}
            </h1>
            {health.state === "loading" ? (
              <p role="status" className="py-6 text-center text-sm text-muted-foreground">
                {t("login.connecting")}
              </p>
            ) : health.state === "error" ? (
              <div role="alert" className="space-y-3 py-4 text-center">
                <p className="text-sm text-destructive">{t("login.unreachable", { url: API_BASE })}</p>
                <Button variant="outline" size="sm" onClick={retry}>
                  {t("common.retry")}
                </Button>
              </div>
            ) : health.health.demo_mode ? (
              <DemoRoles />
            ) : (
              <PasswordForm />
            )}
          </section>
          {health.state === "ready" ? (
            <p className="mt-4 text-center text-caption text-faint tabular-nums">
              {t("login.apiVersion", { version: health.health.version })}
            </p>
          ) : null}
        </div>
      </main>
    </div>
  );
}
