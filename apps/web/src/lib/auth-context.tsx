"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { AUTH_MODE_KEY, SESSION_TOKEN_KEY } from "@/lib/api";
import { fetchMe, logoutRequest, type MeInfo, type SessionInfo } from "@/lib/hooks/useAuthApi";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  isAuthenticated: boolean;
  user: MeInfo | null;
  /** Persists a freshly-issued session (from /auth/login). */
  login: (session: SessionInfo) => void;
  /** Persists the shared admin token for owner-org access. */
  loginAdmin: (token: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Single source of truth for dashboard session auth state, backed by the
 * `veerox_session_token` localStorage key `apiFetch` reads directly. On
 * mount, a stored token is validated (and its org/role/email hydrated) via
 * GET /auth/me rather than trusted blindly — a token surviving in
 * localStorage past a server-side revocation (password reset, member
 * removal) needs to bounce back to /login, not render a dashboard that then
 * 403s on every request.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<MeInfo | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function hydrate() {
      const token = localStorage.getItem(SESSION_TOKEN_KEY);
      const mode = localStorage.getItem(AUTH_MODE_KEY);
      if (!token) {
        if (!cancelled) setStatus("unauthenticated");
        return;
      }
      try {
        if (mode === "admin") {
          const me = await fetchMe();
          if (cancelled) return;
          setUser(me);
          setStatus("authenticated");
          return;
        }
        const me = await fetchMe();
        if (cancelled) return;
        setUser(me);
        setStatus("authenticated");
      } catch {
        if (cancelled) return;
        localStorage.removeItem(SESSION_TOKEN_KEY);
        localStorage.removeItem(AUTH_MODE_KEY);
        setStatus("unauthenticated");
      }
    }
    hydrate();

    function onStorage(e: StorageEvent) {
      if (e.key === SESSION_TOKEN_KEY && !e.newValue) {
        setUser(null);
        setStatus("unauthenticated");
      }
      if (e.key === AUTH_MODE_KEY && !e.newValue) {
        setUser(null);
        setStatus("unauthenticated");
      }
    }
    window.addEventListener("storage", onStorage);
    return () => {
      cancelled = true;
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const login = useCallback((session: SessionInfo) => {
    localStorage.setItem(SESSION_TOKEN_KEY, session.token);
    localStorage.setItem(AUTH_MODE_KEY, "session");
    setUser({
      org_id: session.org_id,
      org_name: session.org_name,
      role: session.role,
      account_user_id: session.account_user_id,
      email: session.email,
      full_name: session.full_name,
      is_superuser: session.is_superuser,
    });
    setStatus("authenticated");
  }, []);

  const loginAdmin = useCallback(async (token: string) => {
    localStorage.setItem(SESSION_TOKEN_KEY, token);
    localStorage.setItem(AUTH_MODE_KEY, "admin");
    const me = await fetchMe();
    setUser(me);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(() => {
    // Best-effort server-side session invalidation — clearing local state
    // and redirecting proceeds regardless of whether this succeeds.
    logoutRequest().catch(() => {});
    localStorage.removeItem(SESSION_TOKEN_KEY);
    localStorage.removeItem(AUTH_MODE_KEY);
    setUser(null);
    setStatus("unauthenticated");
    router.push("/login");
  }, [router]);

  const value = useMemo(
    () => ({ status, isAuthenticated: status === "authenticated", user, login, loginAdmin, logout }),
    [status, user, login, loginAdmin, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
