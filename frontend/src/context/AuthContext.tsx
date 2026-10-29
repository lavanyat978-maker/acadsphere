import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, clearToken, setToken } from "../lib/api";
import type { LoginResponse, Meta, User } from "../lib/types";

interface AuthValue {
  user: User | null;
  meta: Meta | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<LoginResponse>;
  verifyMfa: (mfaToken: string, code: string) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const current = await api<User>("/auth/me");
      setUser(current);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [current, metadata] = await Promise.all([
        api<User>("/auth/me").catch(() => null),
        api<Meta>("/meta").catch(() => null),
      ]);
      if (cancelled) return;
      setUser(current);
      setMeta(metadata);
      setLoading(false);
    })();
    const onUnauthorized = () => setUser(null);
    window.addEventListener("praxis:unauthorized", onUnauthorized);
    return () => {
      cancelled = true;
      window.removeEventListener("praxis:unauthorized", onUnauthorized);
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api<LoginResponse>("/auth/login", {
      method: "POST",
      body: { email, password },
    });
    if (!result.mfa_required && result.user) {
      if (result.access_token) setToken(result.access_token);
      setUser(result.user);
    }
    return result;
  }, []);

  const verifyMfa = useCallback(async (mfaToken: string, code: string) => {
    const result = await api<LoginResponse>("/auth/mfa/verify", {
      method: "POST",
      body: { mfa_token: mfaToken, code },
    });
    if (!result.user) throw new Error("The verification response was incomplete.");
    if (!result.access_token) throw new Error("The verification response was incomplete.");
    setToken(result.access_token);
    setUser(result.user);
    return result.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      clearToken();
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, meta, loading, login, verifyMfa, logout, refresh }),
    [user, meta, loading, login, verifyMfa, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
