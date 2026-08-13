import { defineRouting } from "next-intl/routing";

/**
 * Turkish is the default and carries no prefix: the users are Turkish, the
 * campaign text is Turkish, and `/dashboard` reading as Turkish is the honest
 * default. English lives under `/en`.
 */
export const routing = defineRouting({
  locales: ["tr", "en"],
  defaultLocale: "tr",
  localePrefix: "as-needed",
});

export type Locale = (typeof routing.locales)[number];
