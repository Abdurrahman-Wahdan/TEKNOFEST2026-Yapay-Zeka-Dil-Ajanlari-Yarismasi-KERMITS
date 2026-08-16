/**
 * Turkish number, money and date formatting.
 *
 * Turkish uses "." for thousands and "," for decimals — the opposite of
 * English. `1.234,56 ₺` is one thousand lira in Turkish and is nonsense read as
 * English, so nothing here may fall back to `toFixed()` or a raw template
 * string. Every formatter takes the active locale.
 */

type Locale = "tr" | "en";

/** BCP-47 tag for Intl. Our locale codes happen to match, but not by accident. */
function tag(locale: Locale) {
  return locale === "tr" ? "tr-TR" : "en-GB";
}

/**
 * Precious metals are quoted per gram, and a gram is worth thousands of lira,
 * so two decimals is not the same amount of information it is for a currency:
 * Kuveyt Türk's gold profit of 0,0303 grams renders as "0,03" and loses most
 * of the number. Four is enough to keep every figure the banks actually quote.
 */
const METAL_DIGITS: Record<string, number> = { XAU: 4, XAG: 4 };

export function formatMoney(
  value: number,
  locale: Locale,
  currency = "TRY",
  fractionDigits = METAL_DIGITS[currency] ?? 2,
) {
  return new Intl.NumberFormat(tag(locale), {
    style: "currency",
    currency,
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value);
}

/**
 * A rate, as a percentage.
 *
 * The banks quote these as plain numbers already in percent (3.29 meaning
 * 3.29%), so this does NOT divide by 100 — `Intl`'s `style: "percent"` would,
 * and would turn a 3.29% profit rate into 329%.
 */
export function formatRate(value: number, locale: Locale, fractionDigits = 2) {
  const formatted = new Intl.NumberFormat(tag(locale), {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value);
  return `%${formatted}`;
}

export function formatNumber(
  value: number,
  locale: Locale,
  fractionDigits: number | { min: number; max: number } = 0,
) {
  // A range, not a single figure, so a rate keeps the precision its bank
  // published without padding every other number with dead zeros: 47,4487 and
  // 0,03619 both survive, and 1,00 does not become 1,000000.
  const { min, max } =
    typeof fractionDigits === "number"
      ? { min: fractionDigits, max: fractionDigits }
      : fractionDigits;
  return new Intl.NumberFormat(tag(locale), {
    minimumFractionDigits: min,
    maximumFractionDigits: max,
  }).format(value);
}

/**
 * A compact figure for a chart axis — 1,2 Mn rather than 1.200.000.
 */
export function formatCompact(value: number, locale: Locale) {
  return new Intl.NumberFormat(tag(locale), {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatDate(value: string | Date, locale: Locale) {
  const date = typeof value === "string" ? new Date(value) : value;
  return new Intl.DateTimeFormat(tag(locale), {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Europe/Istanbul",
  }).format(date);
}

/**
 * Days until a campaign ends. Negative means it already has.
 *
 * Compared at Istanbul midnight, not the browser's: a campaign ending "today"
 * means today in Turkey, and a user in another timezone must not see it expire
 * a day early or late.
 */
export function daysUntil(isoDate: string): number {
  const istanbulToday = new Date(
    new Date().toLocaleString("en-US", { timeZone: "Europe/Istanbul" }),
  );
  istanbulToday.setHours(0, 0, 0, 0);
  const end = new Date(isoDate);
  end.setHours(0, 0, 0, 0);
  return Math.round((end.getTime() - istanbulToday.getTime()) / 86_400_000);
}

/**
 * Turkish-safe lowercasing.
 *
 * `"I".toLowerCase()` is "i" in every locale by default, but Turkish needs "ı"
 * — and `"İ".toLowerCase()` gives "i̇" (with a combining dot) unless the locale
 * is passed. Anywhere a bank or product name is folded for comparison, this is
 * the function to use.
 */
export function fold(value: string, locale: Locale) {
  return value.toLocaleLowerCase(tag(locale));
}
