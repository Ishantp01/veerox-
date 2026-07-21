"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";

const TOKEN_KEY = "veerox_admin_token";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  isAuthenticated: boolean;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Single source of truth for admin-token auth state, backed by the same
 * `veerox_admin_token` localStorage key `apiFetch` reads directly. Status
 * starts as "loading" on every render (server and first client paint agree,
 * so there's no hydration mismatch) and resolves to authenticated/
 * unauthenticated in an effect once localStorage is available.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<AuthStatus>("loading");

  useEffect(() => {
    setStatus(localStorage.getItem(TOKEN_KEY) ? "authenticated" : "unauthenticated");

    function onStorage(e: StorageEvent) {
      if (e.key === TOKEN_KEY) {
        setStatus(e.newValue ? "authenticated" : "unauthenticated");
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const login = useCallback((token: string) => {
    localStorage.setItem(TOKEN_KEY, token);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setStatus("unauthenticated");
    router.push("/login");
  }, [router]);

  const value = useMemo(
    () => ({ status, isAuthenticated: status === "authenticated", login, logout }),
    [status, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
