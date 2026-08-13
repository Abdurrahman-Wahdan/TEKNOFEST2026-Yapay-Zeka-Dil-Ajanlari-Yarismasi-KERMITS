import { getRequestConfig } from "next-intl/server";
import { hasLocale } from "next-intl";

import { routing } from "./routing";

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  // Falls back rather than throwing: a hand-typed or stale URL with an unknown
  // locale should render the site in Turkish, not 500.
  const locale = hasLocale(routing.locales, requested)
    ? requested
    : routing.defaultLocale;

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
    // Turkey is the only market, so times are shown in Istanbul regardless of
    // where the browser is. A campaign that "ends today" must mean today in
    // Turkey.
    timeZone: "Europe/Istanbul",
  };
});
