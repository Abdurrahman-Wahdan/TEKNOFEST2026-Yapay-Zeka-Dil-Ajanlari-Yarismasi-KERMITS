"use client";

import { useTranslations } from "next-intl";

import { useTheme } from "@/lib/theme";

import styles from "./Switcher.module.scss";

/**
 * The sidebar's light/dark control: a two-segment switch, matching the locale
 * switch beside it.
 *
 * The store lives in `@/lib/theme` so this and the login screen's icon button
 * read the same state — two components each with their own copy would drift the
 * moment one of them changed how it resolves the OS preference.
 */
export function ThemeToggle() {
  const t = useTranslations("nav");
  const { theme, setTheme } = useTheme();

  return (
    <div className={styles.group} role="group" aria-label={t("theme")}>
      {(["light", "dark"] as const).map((option) => (
        <button
          key={option}
          className={styles.option}
          aria-pressed={theme === option}
          onClick={() => setTheme(option)}
        >
          {option === "light" ? "☀" : "☾"}
        </button>
      ))}
    </div>
  );
}
