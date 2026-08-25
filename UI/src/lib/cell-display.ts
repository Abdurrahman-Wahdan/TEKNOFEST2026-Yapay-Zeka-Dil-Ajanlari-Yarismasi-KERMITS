/**
 * What a table cell says, as text.
 *
 * Extracted from the private `Cell` component in `ProducedTable` because two
 * readers now need the same answer: the person looking at the table, and the
 * assistant being asked about it. A `bank` cell holds `"kuveytturk"` and reads
 * *Kuveyt Türk*; a `money` cell holds `42` and reads `₺42,00`. Serialising the
 * raw cells would hand the agent a table nobody can see, and it would answer
 * about that one instead of the one on screen.
 *
 * `cellText` in `table-filter.ts` is deliberately NOT this: that one is the raw,
 * unformatted search key, where `1.234,56 ₺` would break a numeric match. Both
 * exist on purpose.
 */

import type { CellValue, ResolvedColumn } from "./contract.ts";
import { formatDate, formatMoney, formatNumber, formatRate } from "./format.ts";

type Locale = "tr" | "en";

/** What the table renders as `—`: absent, which is neither zero nor false. */
export function isBlankCell(value: CellValue | undefined): boolean {
  return value === undefined || value === null || value === "";
}

/** The dash the table shows for an absent value. */
export const BLANK_CELL = "—";

/**
 * One cell, as the text a reader sees.
 *
 * The `link` case is the single place this and the table diverge, and it is
 * deliberate: the table renders a "Kaynak" call-to-action because a bare domain
 * reads as the bank's front page, whereas an agent asked to cite a source needs
 * the URL itself. The affordance is the UI's; the datum is the URL.
 */
export function cellDisplayText(
  value: CellValue | undefined,
  column: ResolvedColumn,
  locale: Locale,
  bankLabels?: Record<string, string>,
): string {
  if (isBlankCell(value)) return BLANK_CELL;

  switch (column.type) {
    case "money":
      return typeof value === "number"
        ? formatMoney(value, locale, column.currency)
        : String(value);

    case "percent":
      return typeof value === "number" ? formatRate(value, locale) : String(value);

    case "number":
      return typeof value === "number"
        ? formatNumber(value, locale, column.decimals)
        : String(value);

    case "date":
      return formatDate(String(value), locale);

    case "bank":
      return bankLabels?.[String(value)] ?? String(value);

    case "bool":
      // Absent already returned above, so a `✕` here means a definite no.
      return value === true ? "✓" : "✕";

    default:
      return String(value);
  }
}
