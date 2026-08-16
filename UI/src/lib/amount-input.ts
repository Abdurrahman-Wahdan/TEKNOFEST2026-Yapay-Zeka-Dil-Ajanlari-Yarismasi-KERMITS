/**
 * Grouped, decimal-aware number entry.
 *
 * Turkish uses "." for thousands and "," for decimals -- the opposite of
 * English, and already documented the hard way in `format.ts`. An amount
 * field that hardcodes a comma as the thousands separator is correct for an
 * English reader and nonsense for a Turkish one on the exact same page, so
 * every function here is parameterised on the locale's actual separators
 * rather than assuming either convention.
 *
 * Split from the input component so the parsing/formatting can be tested as
 * plain functions -- the fiddly part of a live-formatting field is never the
 * JSX, it is "what does the string look like after this one keystroke",
 * which is exactly what a unit test is for.
 */

type Locale = "tr" | "en";

export interface Separators {
  /** Thousands separator: "." in Turkish, "," in English. */
  group: string;
  /** Decimal separator: "," in Turkish, "." in English. */
  decimal: string;
}

/** The actual glyphs `Intl` uses for this locale, not an assumption. */
export function localeSeparators(locale: Locale): Separators {
  const parts = new Intl.NumberFormat(locale === "tr" ? "tr-TR" : "en-GB").formatToParts(
    11111.1,
  );
  return {
    group: parts.find((p) => p.type === "group")?.value ?? ",",
    decimal: parts.find((p) => p.type === "decimal")?.value ?? ".",
  };
}

/**
 * Whatever the user just typed or pasted, reduced to a canonical numeric
 * string: ASCII digits, a single "." for the decimal point (never the
 * locale's own decimal glyph), no grouping, no sign. `Number()` and the
 * backend's request params read this directly, so the field's display
 * convention never leaks into the value the rest of the app sees.
 *
 * Only the *first* decimal separator counts -- typing a second one is a slip,
 * not a second decimal point, and is dropped along with any letters or other
 * stray characters a paste might bring in.
 */
export function toCanonical(raw: string, separators: Separators, allowDecimal: boolean): string {
  const decimalIndex = raw.indexOf(separators.decimal);
  const onlyDigits = (s: string) => s.replace(/\D/g, "");

  if (!allowDecimal || decimalIndex === -1) {
    return onlyDigits(raw);
  }

  const whole = onlyDigits(raw.slice(0, decimalIndex));
  const fraction = onlyDigits(raw.slice(decimalIndex + separators.decimal.length));
  return `${whole}.${fraction}`;
}

/** `"1234"` -> `"1.234"` (Turkish) or `"1,234"` (English). No sign, digits only. */
function group(digits: string, groupChar: string): string {
  if (digits === "") return "";
  const chunks: string[] = [];
  for (let end = digits.length; end > 0; end -= 3) {
    chunks.unshift(digits.slice(Math.max(0, end - 3), end));
  }
  return chunks.join(groupChar);
}

/**
 * A canonical numeric string, formatted for display in this locale.
 *
 * A trailing "." (the user just pressed the decimal key) and trailing zeros
 * in the fraction (the user is mid-way through "1.50") both survive here --
 * reformatting to `Number(canonical)` and back would silently eat both, and
 * eating what someone just typed is exactly the bug this field exists to
 * avoid.
 */
export function toDisplay(canonical: string, separators: Separators): string {
  if (canonical === "") return "";
  const dot = canonical.indexOf(".");
  if (dot === -1) return group(canonical, separators.group);

  const whole = canonical.slice(0, dot);
  const fraction = canonical.slice(dot + 1);
  return `${group(whole, separators.group)}${separators.decimal}${fraction}`;
}

/**
 * A canonical value with at least `minDigits` after the point -- "1000000"
 * becomes "1000000.00", never "1000000.0000". Precision is only ever added,
 * never taken away: "1500000.567" stays exactly as typed, because a gram
 * amount genuinely typed to three decimals is not a mistake to round off.
 *
 * Only meant for a display the user is not actively typing into. Applying
 * this on every keystroke would fight the person typing "1500000.5" into
 * "1500000.56" -- it would keep resetting the field to "1500000.50" before
 * the "6" ever lands. Pad on blur (and for the value nobody has touched
 * yet), not while the caret is still in the field.
 */
export function padDecimals(canonical: string, minDigits: number): string {
  if (canonical === "") return canonical;
  const dot = canonical.indexOf(".");
  if (dot === -1) return `${canonical}.${"0".repeat(minDigits)}`;
  const fraction = canonical.slice(dot + 1);
  if (fraction.length >= minDigits) return canonical;
  return canonical + "0".repeat(minDigits - fraction.length);
}

/** How many significant characters (digits + a decimal point) precede `pos`. */
export function countSignificant(display: string, pos: number, decimal: string): number {
  let count = 0;
  for (let i = 0; i < pos && i < display.length; i++) {
    if (/\d/.test(display[i]) || display[i] === decimal) count++;
  }
  return count;
}

/** The caret position landing after the same count of significant characters. */
export function positionAfterSignificant(
  display: string,
  significantCount: number,
  decimal: string,
): number {
  if (significantCount <= 0) return 0;
  let count = 0;
  for (let i = 0; i < display.length; i++) {
    if (/\d/.test(display[i]) || display[i] === decimal) {
      count++;
      if (count === significantCount) return i + 1;
    }
  }
  return display.length;
}
