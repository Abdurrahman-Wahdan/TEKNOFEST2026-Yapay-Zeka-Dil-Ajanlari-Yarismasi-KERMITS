"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, setAccessToken, type TokenPair, type User } from "./api";

/**
 * Where the tokens live, and why.
 *
 * The **access token is kept in memory only**. localStorage is readable by any
 * script on the page, so a single XSS hands over a working credential; a
 * variable in a module dies with the tab.
 *
 * The **refresh token is in localStorage**, which is the deliberate trade-off:
 * without it, every page reload logs the user out. It buys reload survival at
 * the cost of being stealable, which is why the access token it mints is short
 * (30 minutes) and why moving both into httpOnly cookies is the next step when
 * this stops being a local system.
 */
const REFRESH_KEY = "tf26.refresh";

type AuthState = {
  user: User | null;
  /** True until the initial refresh-from-storage settles. */
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (
    email: string,
    password: string,
    displayName: string,
    locale?: "tr" | "en",
  ) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

function readRefresh(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(REFRESH_KEY);
  } catch {
    // Safari in private mode throws on localStorage. Losing session
    // persistence is acceptable; crashing the app is not.
    return null;
  }
}

function writeRefresh(token: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(REFRESH_KEY, token);
    else window.localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* see readRefresh */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const apply = useCallback(async (tokens: TokenPair) => {
    setAccessToken(tokens.access_token);
    writeRefresh(tokens.refresh_token);
    setUser(await api.me());
  }, []);

  const logout = useCallback(() => {
    setAccessToken(null);
    writeRefresh(null);
    setUser(null);
  }, []);

  // On mount: trade a stored refresh token for a session. This is what makes a
  // reload keep you signed in.
  useEffect(() => {
    let cancelled = false;

    // All the state updates happen inside this async function rather than in
    // the effect body. Setting state synchronously in an effect triggers a
    // second render pass before paint on every mount, which is what the
    // set-state-in-effect rule flags.
    async function restore() {
      const stored = readRefresh();
      if (!stored) {
        if (!cancelled) setLoading(false);
        return;
      }
      try {
        const tokens = await api.refresh(stored);
        if (!cancelled) await apply(tokens);
      } catch {
        // Expired or tampered with. Clear it rather than retrying: a bad token
        // will not become good, and looping would hammer the endpoint.
        if (!cancelled) logout();
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
  }, [apply, logout]);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      login: async (email, password) => {
        await apply(await api.login({ email, password }));
      },
      // The locale comes from the page the user signed up on, rather than
      // being hardcoded — someone creating an account on /en was previously
      // stored as a Turkish speaker.
      signup: async (email, password, displayName, locale = "tr") => {
        await apply(
          await api.signup({
            email,
            password,
            display_name: displayName,
            locale,
          }),
        );
      },
      logout,
      refreshUser: async () => setUser(await api.me()),
    }),
    [user, loading, apply, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside <AuthProvider>.");
  }
  return context;
}
