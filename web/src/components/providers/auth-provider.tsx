"use client";

import { useTranslations } from "next-intl";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { toast } from "sonner";

import { useMounted } from "@/hooks/use-mounted";
import { api, ApiError, setUnauthorizedHandler, type Role, type TokenOut } from "@/lib/api";
import {
  clearSession,
  isExpired,
  peekSession,
  subscribeSession,
  writeSession,
  type Session,
} from "@/lib/session";

/**
 * - `loading`: before hydration, or while a stored session is checked against /api/auth/me
 * - `signed-in` / `signed-out`
 * - `offline`: a session exists but the API is unreachable; it is kept so Retry can resume
 */
export type AuthStatus = "loading" | "signed-in" | "signed-out" | "offline";

interface AuthContextValue {
  status: AuthStatus;
  session: Session | null;
  signInDemo: (role: Role) => Promise<void>;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => void;
  revalidate: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** setTimeout fires at once for delays past 2^31-1 ms, so long waits re-arm daily. */
const MAX_TIMER_MS = 24 * 60 * 60 * 1000;

interface Check {
  token: string;
  attempt: number;
  ok: boolean;
}

function sessionFromToken(out: TokenOut): Session {
  return {
    token: out.access_token,
    expiresAt: out.expires_at,
    userId: out.user.user_id,
    roles: out.user.roles,
    demoMode: out.demo_mode,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const t = useTranslations("auth");
  const mounted = useMounted();
  const session = useSyncExternalStore(subscribeSession, peekSession, () => null);
  const [attempt, setAttempt] = useState(0);
  const [check, setCheck] = useState<Check | null>(null);
  const [timerTick, setTimerTick] = useState(0);

  const end = useCallback(
    (notify: boolean) => {
      clearSession();
      if (notify) toast.info(t("ended"), { id: "session-ended" });
    },
    [t],
  );

  // A 401 on any authenticated call means the server no longer accepts the token, unless the
  // call carried a token this tab no longer holds: one still in flight when its user signed out
  // (sign-out revokes it) or signed in again since. That says nothing about the session now.
  useEffect(
    () =>
      setUnauthorizedHandler((refused) => {
        const current = peekSession();
        if (!current || (refused !== null && refused !== current.token)) return;
        end(true);
      }),
    [end],
  );

  // Check a stored session once per token (and again on Retry). /me is the truth for roles.
  const token = session?.token ?? null;
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    api
      .me(controller.signal)
      .then((me) => {
        const stored = peekSession();
        if (stored?.token === token) {
          writeSession({
            ...stored,
            userId: me.user_id,
            roles: me.roles,
            demoMode: me.demo_mode,
            expiresAt: me.expires_at,
          });
        }
        setCheck({ token, attempt, ok: true });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) return; // end() already ran
        setCheck({ token, attempt, ok: false });
      });
    return () => controller.abort();
  }, [token, attempt]);

  // Sign out the moment the token expires.
  const expiresAt = session?.expiresAt ?? null;
  useEffect(() => {
    if (!expiresAt) return;
    const left = Date.parse(expiresAt) - Date.now();
    const timer = setTimeout(
      () => {
        const stored = peekSession();
        if (stored && isExpired(stored)) end(true);
        else setTimerTick((n) => n + 1); // a long wait: re-arm
      },
      Math.max(0, Math.min(left, MAX_TIMER_MS)),
    );
    return () => clearTimeout(timer);
  }, [expiresAt, end, timerTick]);

  let status: AuthStatus;
  if (!mounted) status = "loading";
  else if (!session) status = "signed-out";
  else if (check?.token === session.token && check.attempt === attempt) {
    status = check.ok ? "signed-in" : "offline";
  } else status = "loading";

  const adopt = useCallback(
    (out: TokenOut) => {
      const next = sessionFromToken(out);
      writeSession(next);
      setCheck({ token: next.token, attempt, ok: true }); // just issued: no need to wait for /me
    },
    [attempt],
  );

  const signInDemo = useCallback(async (role: Role) => adopt(await api.demoLogin(role)), [adopt]);
  const signIn = useCallback(
    async (username: string, password: string) => adopt(await api.login(username, password)),
    [adopt],
  );
  // Signed out here at once; the server is asked to revoke the token on the side. Whatever it
  // answers (200, a 401 for a token already dead, no answer at all) changes nothing here.
  const signOut = useCallback(() => {
    const token = peekSession()?.token ?? null;
    end(false);
    if (token) void api.logout(token).catch(() => undefined);
  }, [end]);
  const revalidate = useCallback(() => setAttempt((n) => n + 1), []);

  const live = status === "signed-in" || status === "offline" ? session : null;
  const value = useMemo<AuthContextValue>(
    () => ({ status, session: live, signInDemo, signIn, signOut, revalidate }),
    [status, live, signInDemo, signIn, signOut, revalidate],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
