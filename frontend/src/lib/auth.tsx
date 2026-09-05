// JWT auth context + helpers (PRD §10 Day 19).
//
// Token persistence is intentionally localStorage rather than cookie: the
// FastAPI side uses Bearer tokens and the demo runs entirely on the same
// origin via Vite's proxy, so we don't need CSRF protection or HttpOnly.
//
// Day-23 deploy hardening will tighten this (Secure cookie + refresh
// rotation). For Day 19 the contract is: token saved → Authorization
// header set on every subsequent fetch → protected route gate opens.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { HttpError } from "./api";

const TOKEN_KEY = "sentinelg.jwt";

export interface AuthUser {
  user_id: string;
  email: string;
  role: string;
}

interface AuthContextValue {
  token: string | null;
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, role?: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in_seconds: number;
}

async function postAuth<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new HttpError(res.status, "POST", path, text || res.statusText);
  }
  return (await res.json()) as T;
}

async function fetchMe(token: string): Promise<AuthUser> {
  const res = await fetch("/api/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new HttpError(res.status, "GET", "/auth/me", text || res.statusText);
  }
  return (await res.json()) as AuthUser;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  });
  const [user, setUser] = useState<AuthUser | null>(null);

  // Hydrate the user object on first mount when we have a stored token —
  // verifies the token is still valid; a 401 here forces logout.
  useEffect(() => {
    if (!token) { setUser(null); return; }
    let cancelled = false;
    fetchMe(token).then((u) => { if (!cancelled) setUser(u); })
      .catch(() => {
        if (!cancelled) {
          try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
          setToken(null);
          setUser(null);
        }
      });
    return () => { cancelled = true; };
  }, [token]);

  const login = useCallback(async (email: string, password: string) => {
    const resp = await postAuth<TokenResponse>("/auth/login", { email, password });
    try { localStorage.setItem(TOKEN_KEY, resp.access_token); } catch { /* ignore */ }
    setToken(resp.access_token);
  }, []);

  const register = useCallback(async (email: string, password: string, role?: string) => {
    // Omit `role` from the body when caller doesn't supply one — the backend
    // (backend/app/auth/models.py UserRole) only accepts
    // {credit_officer, investigator, auditor, admin} and defaults to
    // credit_officer. Sending an arbitrary string like "analyst" returns 422.
    const body: Record<string, unknown> = { email, password };
    if (role) body.role = role;
    await postAuth("/auth/register", body);
    await login(email, password);
  }, [login]);

  const logout = useCallback(() => {
    try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    token, user,
    isAuthenticated: Boolean(token),
    login, register, logout,
  }), [token, user, login, register, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/** Convenience: returns auth headers if a token is set, otherwise empty. */
export function authHeaders(): HeadersInit {
  try {
    const tok = localStorage.getItem(TOKEN_KEY);
    return tok ? { Authorization: `Bearer ${tok}` } : {};
  } catch {
    return {};
  }
}
