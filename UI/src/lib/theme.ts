"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * The light/dark theme, as external state.
 *
 * The theme lives in the DOM (a `.dark` class on <html>) and localStorage, and
 * the inline script in the locale layout resolves it before React ever runs.
 * So it is read with `useSyncExternalStore` rather than mirrored into
 * `useState` and synced back in an effect — an effect would set state on every
 * mount, costing a second render before paint.
 *
 * The class is what Tailwind's `dark:` variant keys off (see the
 * `@custom-variant` in tailwind.css), so the palette and the utilities can
 * never disagree about which theme is active.
 */
export type Theme = "light" | "dark";

const KEY = "tf26.theme";
const EVENT = "tf26:themechange";

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  // `storage` fires in *other* tabs, so switching theme in one updates the rest.
  window.addEventListener("storage", onChange);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
    media.removeEventListener("change", onChange);
  };
}

function getSnapshot(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/**
 * The server cannot know the user's theme, so it renders the light state and
 * the client corrects it on hydration. Guessing here would produce markup that
 * disagrees with the client's.
 */
function getServerSnapshot(): Theme {
  return "light";
}

export function setTheme(next: Theme) {
  document.documentElement.classList.toggle("dark", next === "dark");
  // A cookie as well as localStorage: the cookie is what the server layout
  // reads, so the `.dark` class is already in the HTML it sends. That is what
  // removes the first-paint flash — localStorage cannot be read on the server,
  // and a script that reads it has to run after the document exists.
  document.cookie = `${KEY}=${next}; path=/; max-age=31536000; samesite=lax`;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* private mode — the choice just does not survive the session */
  }
  // `storage` does not fire in the tab that wrote it, so this is what tells the
  // current tab to re-read.
  window.dispatchEvent(new Event(EVENT));
}

/** `[theme, setTheme, toggle]` for any component that needs the theme. */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const toggle = useCallback(
    () => setTheme(theme === "dark" ? "light" : "dark"),
    [theme],
  );
  return { theme, setTheme, toggle };
}
