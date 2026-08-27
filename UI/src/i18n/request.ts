import { getRequestConfig } from "next-intl/server";
import { hasLocale } from "next-intl";

import trMessages from "../../messages/tr.json";
import { routing } from "./routing";

const messages = {
  tr: trMessages,
};

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  // Falls back rather than throwing: a hand-typed or stale URL with an unknown
  // locale should render the site in Turkish, not 500. With Turkish the only
  // locale this is now the path every `/en/...` bookmark takes.
  const locale = hasLocale(routing.locales, requested)
    ? requested
    : routing.defaultLocale;

  return {
    locale,
    // Explicit imports make message edits part of Turbopack's dependency graph.
    // A computed JSON import kept the old catalogue alive across Fast Refresh,
    // leaving newly added controls rendered as raw `chat.someKey` strings.
    messages: messages[locale],
    // Turkey is the only market, so times are shown in Istanbul regardless of
    // where the browser is. A campaign that "ends today" must mean today in
    // Turkey.
    timeZone: "Europe/Istanbul",
  };
});
