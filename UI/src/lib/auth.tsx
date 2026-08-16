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
 * The **access token is kept in memory only**. localStorage/sessionStorage are
 * readable by any script on the page, so a single XSS hands over a working
 * credential; a variable in a module dies with the tab.
 *
 * The **refresh token goes in localStorage or sessionStorage, chosen by
 * "Keep me signed in"**. Both buy reload survival at the cost of the token
 * being stealable, which is why the access token it mints is short (30
 * minutes) and why moving both into httpOnly cookies is the next step when
 * this stops being a local system. The difference between the two is what the
 * checkbox is actually for:
 *   - checked   -> localStorage: survives closing the browser entirely, so
 *                  coming back later (even on a phone that was left signed
 *                  in) is still signed in.
 *   - unchecked -> sessionStorage: survives a reload or navigating away and
 *                  back, but is gone once the browser/tab is closed — so
 *                  leaving it unchecked on a shared or borrowed device
 *                  doesn't leave a session for the next person to find.
 */
const REFRESH_KEY = "tf26.refresh";

type AuthState = {
  user: User | null;
  /** True until the initial refresh-from-storage settles. */
  loading: boolean;
  login: (email: string, password: string, remember?: boolean) => Promise<void>;
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

/** Reads whichever storage has it, and says which one — `refresh` rotates the
 *  token on every call, and the new one has to go back to the same storage
 *  the user originally chose, not silently switch to the default. */
function readRefresh(): { token: string; remember: boolean } | null {
  if (typeof window === "undefined") return null;
  try {
    const local = window.localStorage.getItem(REFRESH_KEY);
    if (local) return { token: local, remember: true };
    const session = window.sessionStorage.getItem(REFRESH_KEY);
    if (session) return { token: session, remember: false };
    return null;
  } catch {
    // Safari in private mode throws on localStorage. Losing session
    // persistence is acceptable; crashing the app is not.
    return null;
  }
}

/** Writes to the storage `remember` selects and clears the other one, so a
 *  session started with one choice can't linger in both. */
function writeRefresh(token: string | null, remember: boolean) {
  if (typeof window === "undefined") return;
  try {
    const store = remember ? window.localStorage : window.sessionStorage;
    const other = remember ? window.sessionStorage : window.localStorage;
    if (token) store.setItem(REFRESH_KEY, token);
    else store.removeItem(REFRESH_KEY);
    other.removeItem(REFRESH_KEY);
  } catch {
    /* see readRefresh */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const apply = useCallback(async (tokens: TokenPair, remember: boolean) => {
    setAccessToken(tokens.access_token);
    writeRefresh(tokens.refresh_token, remember);
    setUser(await api.me());
  }, []);

  const logout = useCallback(() => {
    setAccessToken(null);
    writeRefresh(null, true);
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
        const tokens = await api.refresh(stored.token);
        if (!cancelled) await apply(tokens, stored.remember);
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
      login: async (email, password, remember = true) => {
        await apply(await api.login({ email, password }), remember);
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
          true,
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
