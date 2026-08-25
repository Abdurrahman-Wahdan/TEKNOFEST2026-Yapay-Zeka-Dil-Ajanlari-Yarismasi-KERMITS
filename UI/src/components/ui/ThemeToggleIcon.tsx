"use client";

import { useTranslations } from "next-intl";

import { ThemeGlyph } from "@/components/ui/ThemeGlyph";
import { useTheme } from "@/lib/theme";

/**
 * A single icon button that flips light/dark.
 *
 * The icon shows the theme you will get, not the one you are in — a moon means
 * "switch to dark". That is the convention users read fastest, and the
 * `aria-label` says it in words so the meaning does not depend on recognising
 * the glyph — "Tema: Koyu", the same phrasing the in-app toggles in
 * `vision/components/VuiThemeToggle` and `SidenavActions` use.
 *
 * Styled with Tailwind rather than an SCSS module because it sits on the login
 * screen, which is Tailwind territory. It is `fixed` so it stays put over both
 * the form column and the hero image, and carries its own translucent
 * background so it stays legible against a photograph.
 */
export function ThemeToggleIcon({ className = "" }: { className?: string }) {
  const t = useTranslations("nav");
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const label = `${t("theme")}: ${next === "dark" ? t("themeDark") : t("themeLight")}`;

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      className={`grid h-10 w-10 place-items-center rounded-full border border-border bg-card/60 text-foreground backdrop-blur-md transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${className}`}
    >
      {/* Shared with the dashboard's toggles — see ThemeGlyph. */}
      <ThemeGlyph theme={theme} />
    </button>
  );
}
