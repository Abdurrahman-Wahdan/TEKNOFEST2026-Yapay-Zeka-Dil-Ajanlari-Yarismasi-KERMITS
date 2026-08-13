"use client";

import { Moon, Sun } from "lucide-react";

import type { Theme } from "@/lib/theme";

/**
 * The light/dark icon, defined once for the whole app.
 *
 * Every theme switch renders this — the one on the sign-in and sign-up screens
 * and the two inside the Vision UI dashboard shell. It exists so the icon
 * cannot drift between them: before this, the dashboard used a Material Icons
 * ligature while the auth screens used lucide, so the same control had two
 * different glyphs depending on the page.
 *
 * lucide-react is the app's icon set. Anything new should draw from it rather
 * than MUI's `<Icon>`, so the icons read as one family the way the colours do.
 *
 * The glyph shows the theme you will GET, not the one you are in — a moon means
 * "switch to dark". Callers pair it with an `aria-label` saying so in words, so
 * the meaning never depends on recognising the picture.
 */
export function ThemeGlyph({ theme, size = 20 }: { theme: Theme; size?: number }) {
  const Icon = theme === "dark" ? Sun : Moon;
  return <Icon size={size} aria-hidden="true" />;
}
