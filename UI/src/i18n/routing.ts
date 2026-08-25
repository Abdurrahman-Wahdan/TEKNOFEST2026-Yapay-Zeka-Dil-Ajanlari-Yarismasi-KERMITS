import { defineRouting } from "next-intl/routing";

/**
 * One locale. Turkish is the only language the site ships in — the English
 * catalogue and the navbar's language toggle were both removed on 2026-08-25.
 *
 * The prefix stays explicit even with a single locale. The App Router only has
 * locale-segment routes (`/[locale]/...`); leaving Turkish unprefixed makes
 * Next's internal rewrite of `/compare` redirect back to `/compare` and loop.
 * Keeping it also means every existing `/tr/...` link and bookmark still
 * resolves.
 */
export const routing = defineRouting({
  locales: ["tr"],
  defaultLocale: "tr",
  localePrefix: "always",
});

export type Locale = (typeof routing.locales)[number];
