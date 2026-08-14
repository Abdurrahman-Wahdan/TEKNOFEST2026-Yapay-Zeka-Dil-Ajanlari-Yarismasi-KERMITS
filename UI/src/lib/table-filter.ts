/**
 * Filtering and sorting for AI-produced tables.
 *
 * All of it runs client-side over the rows already in hand. The producer sends
 * a bounded set (`MAX_ROWS`), so there is no pagination round-trip and no
 * server to keep in step — a filter change is a re-render, not a request.
 *
 * Kept out of the components and free of React so the awkward parts — Turkish
 * folding, mixed-type sorting, what "empty" means — can be tested directly.
 */

// Explicit .ts extensions: these modules are exercised directly by
// `node --test`, whose ESM resolver needs a real filename. The bundler resolves
// them the same way, and `allowImportingTsExtensions` keeps tsc happy.
import { fold } from "./format.ts";
import type { CellValue, ResolvedColumn, Row } from "./contract.ts";

type Locale = "tr" | "en";

export interface FilterState {
  /** Free text, matched across every text-ish column. */
  search: string;
  /** Column key -> the values the user ticked. Empty array means "no filter". */
  values: Record<string, string[]>;
  /** Column key -> numeric bounds, either end optional. */
  ranges: Record<string, { min?: number; max?: number }>;
  /** Column keys the user switched off. */
  hidden: string[];
}

export const EMPTY_FILTERS: FilterState = {
  search: "",
  values: {},
  ranges: {},
  hidden: [],
};

export interface SortState {
  key: string;
  direction: "asc" | "desc";
}

/** Types whose values are worth offering as a tick-list. */
const CATEGORICAL = new Set(["bank", "badge", "bool", "text"]);
/** Types that compare as numbers. */
const NUMERIC = new Set(["money", "percent", "number"]);

function isBlank(value: CellValue | undefined): boolean {
  return value === undefined || value === null || value === "";
}

/** A cell as a string, for search and grouping. Never "null" or "undefined". */
export function cellText(value: CellValue | undefined): string {
  if (isBlank(value)) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/**
 * The distinct values in a column, in display order.
 *
 * Used to build a multiselect. Capped: a "text" column with 400 unique
 * sentences is not a tick-list, and offering one would be unusable — the free
 * text search covers that case instead.
 */
export const MAX_DISTINCT = 25;

export function distinctValues(rows: readonly Row[], key: string): string[] {
  const seen = new Set<string>();
  for (const row of rows) {
    const text = cellText(row.cells[key]);
    if (text !== "") seen.add(text);
    if (seen.size > MAX_DISTINCT) return [];
  }
  return [...seen].sort((a, b) => a.localeCompare(b, "tr"));
}

/**
 * Which filter widget, if any, a column earns.
 *
 * Driven entirely by the resolved column — no per-page configuration, which is
 * what lets one filter bar serve every topic page.
 */
export type FilterKind = "select" | "range" | "none";

export function filterKind(
  column: ResolvedColumn,
  rows: readonly Row[],
): FilterKind {
  if (NUMERIC.has(column.type)) return "range";
  if (!column.filterable) return "none";
  if (!CATEGORICAL.has(column.type)) return "none";
  return distinctValues(rows, column.key).length > 0 ? "select" : "none";
}

/** Columns whose text the free-text search should look at. */
export function searchableKeys(columns: readonly ResolvedColumn[]): string[] {
  return columns
    .filter((c) => c.type === "text" || c.type === "badge" || c.type === "bank")
    .map((c) => c.key);
}

export function applyFilters(
  rows: readonly Row[],
  columns: readonly ResolvedColumn[],
  state: FilterState,
  locale: Locale = "tr",
): Row[] {
  const keys = searchableKeys(columns);
  const needle = fold(state.search.trim(), locale);

  return rows.filter((row) => {
    if (needle !== "") {
      const hit = keys.some((key) =>
        fold(cellText(row.cells[key]), locale).includes(needle),
      );
      if (!hit) return false;
    }

    for (const [key, selected] of Object.entries(state.values)) {
      if (selected.length === 0) continue;
      if (!selected.includes(cellText(row.cells[key]))) return false;
    }

    for (const [key, bounds] of Object.entries(state.ranges)) {
      if (bounds.min === undefined && bounds.max === undefined) continue;
      const raw = row.cells[key];
      // A row with no value in a bounded column is excluded: the user asked
      // for "under 2000", and "we don't know" is not under 2000.
      if (isBlank(raw)) return false;
      const value = typeof raw === "number" ? raw : Number(raw);
      if (Number.isNaN(value)) return false;
      if (bounds.min !== undefined && value < bounds.min) return false;
      if (bounds.max !== undefined && value > bounds.max) return false;
    }

    return true;
  });
}

/**
 * Sort rows by one column.
 *
 * Blanks always sink to the bottom regardless of direction — a column of
 * missing values crowding the top of a descending sort tells the user nothing,
 * and "unknown" is not the largest value.
 */
export function sortRows(
  rows: readonly Row[],
  sort: SortState | null,
  columns: readonly ResolvedColumn[],
  locale: Locale = "tr",
): Row[] {
  if (!sort) return [...rows];
  const column = columns.find((c) => c.key === sort.key);
  if (!column) return [...rows];

  const sign = sort.direction === "asc" ? 1 : -1;

  return [...rows].sort((a, b) => {
    const left = a.cells[sort.key];
    const right = b.cells[sort.key];

    const leftBlank = isBlank(left);
    const rightBlank = isBlank(right);
    if (leftBlank && rightBlank) return 0;
    if (leftBlank) return 1;
    if (rightBlank) return -1;

    if (NUMERIC.has(column.type)) {
      const l = typeof left === "number" ? left : Number(left);
      const r = typeof right === "number" ? right : Number(right);
      if (Number.isNaN(l) || Number.isNaN(r)) return 0;
      return (l - r) * sign;
    }

    if (column.type === "bool") {
      return ((left === true ? 1 : 0) - (right === true ? 1 : 0)) * sign;
    }

    // ISO dates sort correctly as strings, which is why the contract only ever
    // types a date column when the values are ISO.
    return cellText(left).localeCompare(cellText(right), locale === "tr" ? "tr" : "en") * sign;
  });
}
