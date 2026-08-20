import { defineRouting } from "next-intl/routing";

/**
 * Both locales use an explicit prefix. In particular, the App Router only has
 * locale-segment routes (`/[locale]/...`); leaving Turkish unprefixed makes
 * Next's internal rewrite of `/compare` redirect back to `/compare` and loop.
 */
export const routing = defineRouting({
  locales: ["tr", "en"],
  defaultLocale: "tr",
  localePrefix: "always",
});

export type Locale = (typeof routing.locales)[number];
