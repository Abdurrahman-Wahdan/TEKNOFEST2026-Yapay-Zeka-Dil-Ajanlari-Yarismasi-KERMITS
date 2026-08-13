"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Link, usePathname } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";

import { LocaleSwitch } from "./LocaleSwitch";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./AppShell.module.scss";

/** The nav. `key` indexes into the `nav` message catalog. */
const NAV = [
  { href: "/dashboard", key: "dashboard" },
  { href: "/banks", key: "banks" },
  { href: "/compare", key: "compare" },
  { href: "/ai-overview", key: "aiOverview" },
  { href: "/chat", key: "chat" },
  { href: "/settings", key: "settings" },
] as const;

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations("nav");
  const tApp = useTranslations("app");
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  return (
    <div className={styles.shell}>
      <a href="#main" className={styles.skip}>
        {tApp("name")}
      </a>

      <aside className={styles.sidebar} data-open={open}>
        <div className={styles.brand}>
          <span className={styles.mark}>TF</span>
          <span className={styles.brandText}>
            <strong>{tApp("name")}</strong>
            <small>{tApp("tagline")}</small>
          </span>
        </div>

        <nav className={styles.nav}>
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={styles.navLink}
              // `usePathname` from @/i18n/navigation returns the path without
              // the locale prefix, so this comparison works in both languages.
              aria-current={pathname === item.href ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              {t(item.key)}
            </Link>
          ))}
        </nav>

        <div className={styles.sidebarFooter}>
          <LocaleSwitch />
          <ThemeToggle />
          {user && (
            <button className={styles.signOut} onClick={logout}>
              {t("signOut")}
            </button>
          )}
        </div>
      </aside>

      {/* Closes the drawer when the sidebar is open over the content on mobile. */}
      {open && (
        <button
          className={styles.scrim}
          aria-label="Close navigation"
          onClick={() => setOpen(false)}
        />
      )}

      <div className={styles.main}>
        <header className={styles.topbar}>
          <button
            className={styles.menuButton}
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label="Menu"
          >
            <span className={styles.menuIcon} aria-hidden="true" />
          </button>
          {user && <span className={styles.who}>{user.display_name || user.email}</span>}
        </header>

        <main id="main" className={styles.content}>
          {children}
        </main>
      </div>
    </div>
  );
}
