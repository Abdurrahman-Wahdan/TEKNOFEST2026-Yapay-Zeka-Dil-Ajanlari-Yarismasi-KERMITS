"use client";

import { useLocale, useTranslations } from "next-intl";
import { useParams } from "next/navigation";
import { useTransition } from "react";

import { usePathname, useRouter } from "@/i18n/navigation";
import { routing, type Locale } from "@/i18n/routing";

import styles from "./Switcher.module.scss";

const LABELS: Record<string, string> = { tr: "TR", en: "EN" };

/**
 * Switches locale while staying on the current page.
 *
 * `router.replace(pathname, {locale})` rather than a link to `/en`: swapping
 * the language should not send someone back to the dashboard from the bank page
 * they were reading, and should not add a history entry per toggle.
 */
export function LocaleSwitch() {
  const t = useTranslations("nav");
  const active = useLocale();
  const pathname = usePathname();
  const params = useParams();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function choose(next: Locale) {
    if (next === active) return;
    startTransition(() => {
      // `params` carries any dynamic segment of the current route (a bank slug,
      // a chat id); without it a locale switch on /banks/kuveytturk 404s.
      router.replace(
        // @ts-expect-error -- pathname and params are correlated at runtime,
        // which the typed-routes signature cannot express.
        { pathname, params },
        { locale: next },
      );
    });
  }

  return (
    <div className={styles.group} role="group" aria-label={t("language")}>
      {routing.locales.map((locale) => (
        <button
          key={locale}
          className={styles.option}
          aria-pressed={active === locale}
          disabled={pending}
          onClick={() => choose(locale)}
        >
          {LABELS[locale]}
        </button>
      ))}
    </div>
  );
}
