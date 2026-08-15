/**
 * The contract for AI-produced page components.
 *
 * The API stores and forwards `{type, props}` without validating `props` — on
 * purpose, because every component's prop schema lives here in TypeScript and
 * duplicating them in Python would guarantee the two drift. That makes this
 * file the *only* validator in the system. Everything it rejects has to be
 * rejected visibly: a component the producer got wrong must show up as a
 * component the producer got wrong, never as a blank space.
 *
 * The design rule that shapes all of it: **only `rows` is genuinely required**.
 * A producer that sends nothing but rows still gets a correct table. Every
 * other field has a defensible default, because the cost of a missing `columns`
 * array should be a slightly plainer table, not an error.
 */

import { z } from "zod";

/**
 * How many rows we will render from one table.
 *
 * Filtering and sorting run client-side over the rows already in hand, which is
 * what makes them feel instant — but it also means a runaway table freezes the
 * tab. Past this we render the first N and say so, rather than dropping the
 * table (which loses real content) or rendering all of it (which loses the tab).
 */
export const MAX_ROWS = 500;

/** How many components we will render from one response. */
export const MAX_COMPONENTS = 8;

export const COLUMN_TYPES = [
  "text",
  "money",
  "percent",
  "number",
  "date",
  "bank",
  "link",
  "badge",
  "bool",
] as const;

export type ColumnType = (typeof COLUMN_TYPES)[number];

/**
 * The bank keys the corpus and the registry use.
 *
 * Only a fallback: callers that have already loaded `GET /api/banks` should
 * pass the live set into `resolveTable`, so a new bank starts rendering as a
 * bank without a release here. This constant exists so the pure functions stay
 * pure and testable with no network.
 */
export const KNOWN_BANKS = new Set([
  "kuveytturk",
  "albaraka",
  "vakif",
  "emlak",
  "dunya",
  "ziraat",
  "turkiyefinans",
  "hayat",
  "tom",
  "adil",
]);

// ----- schemas -----

/**
 * A column declaration.
 *
 * `type` is a plain string rather than an enum: an unrecognised type must not
 * fail the table. Validating it here would turn the producer inventing
 * "currency" instead of "money" into a blank page. An unknown type is ignored
 * and the type inferred from the values instead, which beats falling back to
 * text — a "currency" column full of numbers still right-aligns and sorts.
 */
const ColumnSchema = z.object({
  key: z.string().min(1),
  label: z.string().optional(),
  type: z.string().optional(),
  currency: z.string().optional(),
  align: z.enum(["left", "center", "right"]).optional(),
  sortable: z.boolean().optional(),
  filterable: z.boolean().optional(),
});

/** What can sit in a cell. `null` is allowed and means "not found". */
const CellValueSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);

const RowSchema = z.object({
  cells: z.record(z.string(), CellValueSchema),
  cite_url: z.string().optional(),
});

export const TablePropsSchema = z.object({
  id: z.string().optional(),
  /** Rendered raw, never through `t()` — see the note on i18n below. */
  title: z.string().optional(),
  subtitle: z.string().optional(),
  notes: z.string().optional(),
  columns: z.array(ColumnSchema).optional(),
  rows: z.array(RowSchema),
});

export type TableProps = z.infer<typeof TablePropsSchema>;
export type Column = z.infer<typeof ColumnSchema>;
export type Row = z.infer<typeof RowSchema>;
export type CellValue = z.infer<typeof CellValueSchema>;

export const ComponentSchema = z.object({
  type: z.string().min(1),
  props: z.unknown(),
});

export const ComponentsResponseSchema = z.object({
  category: z.string(),
  generated_at: z.string().default(""),
  source: z.string().default("fixture"),
  components: z.array(ComponentSchema).default([]),
});

export type ComponentSpec = z.infer<typeof ComponentSchema>;
export type ComponentsResponse = z.infer<typeof ComponentsResponseSchema>;

// ----- inference -----

const ISO_DATE = /^\d{4}-\d{2}-\d{2}(T|$)/;

/**
 * Guess a column's type from the values actually present in it.
 *
 * Only ever called for a column the producer left untyped. The order of the
 * checks is the whole algorithm: `bool` before `number` because JS booleans are
 * not numbers but are truthy-adjacent, and `date` before `text` because an
 * ISO date is a string and would otherwise sort alphabetically — which is
 * correct for ISO-8601 by luck, and wrong the moment a producer sends
 * "14.08.2026".
 *
 * A column of mixed types falls through to `text`, which renders every value
 * as written. That is the honest answer: we do not know what it is, so we do
 * not reformat it.
 */
export function inferColumnType(
  values: readonly CellValue[],
  knownBanks: ReadonlySet<string> = KNOWN_BANKS,
): ColumnType {
  const present = values.filter((v) => v !== null && v !== undefined && v !== "");
  if (present.length === 0) return "text";

  if (present.every((v) => typeof v === "boolean")) return "bool";
  if (present.every((v) => typeof v === "number")) return "number";

  const strings = present.filter((v): v is string => typeof v === "string");
  if (strings.length !== present.length) return "text";

  if (strings.every((v) => knownBanks.has(v))) return "bank";
  if (strings.every((v) => /^https?:\/\//i.test(v))) return "link";
  if (strings.every((v) => ISO_DATE.test(v))) return "date";

  return "text";
}

/**
 * Build columns from the rows when the producer sent none.
 *
 * Keys are collected in first-seen order across every row, not just the first
 * one — a producer that omits an empty field from row 1 and fills it in row 4
 * still gets that column, in the position it first appeared.
 */
export function inferColumns(
  rows: readonly Row[],
  knownBanks: ReadonlySet<string> = KNOWN_BANKS,
): Column[] {
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row.cells)) {
      if (!seen.has(key)) {
        seen.add(key);
        keys.push(key);
      }
    }
  }

  return keys.map((key) => ({
    key,
    label: key,
    type: inferColumnType(
      rows.map((r) => r.cells[key] ?? null),
      knownBanks,
    ),
  }));
}

// ----- normalisation -----

/** A column with every optional resolved, ready to render. */
export interface ResolvedColumn {
  key: string;
  label: string;
  type: ColumnType;
  currency: string;
  align: "left" | "center" | "right";
  sortable: boolean;
  filterable: boolean;
  /** True when we guessed the type rather than being told it. */
  inferred: boolean;
  /**
   * Decimal places for a `number` column, when the default whole number would
   * throw information away. An FX rate is the case: banks quote to four or
   * five places and the difference between two banks lives there, so rounding
   * 47,4487 to 47 does not just look wrong, it erases the comparison.
   */
  decimals?: { min: number; max: number };
}

/**
 * Something worth telling the user about the data — not an error.
 *
 * A code and its numbers, never a sentence: this module is pure and has no
 * locale, so a message built here could only ever be in one language. The UI
 * translates these through `components.warning.*` in both message files.
 */
export type TableWarning =
  | { code: "truncated"; total: number; shown: number }
  | { code: "unknownColumnType"; column: string; type: string }
  | { code: "emptyColumns"; columns: string[] };

export interface ResolvedTable {
  id: string;
  title: string;
  subtitle: string;
  notes: string;
  columns: ResolvedColumn[];
  rows: Row[];
  warnings: TableWarning[];
  /** True when no row carried a citation — dev-visible, see DataTable. */
  uncited: boolean;
}

function isColumnType(value: string): value is ColumnType {
  return (COLUMN_TYPES as readonly string[]).includes(value);
}

/** Numbers and dates read better right-aligned; everything else left. */
function defaultAlign(type: ColumnType): "left" | "center" | "right" {
  if (type === "money" || type === "percent" || type === "number") return "right";
  if (type === "bool") return "center";
  return "left";
}

/**
 * Turn whatever the producer sent into something renderable.
 *
 * This is where the contract's forgiveness lives. Every branch here is a case
 * the producer got wrong or left out, resolved into a sane default and — where
 * the user would otherwise be misled — recorded in `warnings`.
 */
export function resolveTable(
  props: TableProps,
  knownBanks: ReadonlySet<string> = KNOWN_BANKS,
): ResolvedTable {
  const warnings: TableWarning[] = [];

  let rows = props.rows;
  if (rows.length > MAX_ROWS) {
    warnings.push({ code: "truncated", total: rows.length, shown: MAX_ROWS });
    rows = rows.slice(0, MAX_ROWS);
  }

  const declared = props.columns ?? [];
  const columnsInferred = declared.length === 0;
  const source = columnsInferred ? inferColumns(rows, knownBanks) : declared;

  const columns: ResolvedColumn[] = source.map((column) => {
    const declaredType = column.type;
    const known = declaredType !== undefined && isColumnType(declaredType);
    if (declaredType !== undefined && !known) {
      warnings.push({
        code: "unknownColumnType",
        column: column.label ?? column.key,
        type: declaredType,
      });
    }

    const type: ColumnType = known
      ? (declaredType as ColumnType)
      : inferColumnType(
          rows.map((r) => r.cells[column.key] ?? null),
          knownBanks,
        );

    return {
      key: column.key,
      label: column.label ?? column.key,
      type,
      currency: column.currency ?? "TRY",
      align: column.align ?? defaultAlign(type),
      // A bank or badge column is worth filtering whether or not the producer
      // remembered to say so; those are the two that are always categorical.
      filterable: column.filterable ?? (type === "bank" || type === "badge"),
      // Anything ordered is worth sorting. Text is not, by default: sorting a
      // paragraph column is noise.
      sortable:
        column.sortable ??
        ["money", "percent", "number", "date"].includes(type),
      // True when nothing told us this type: either the whole column list was
      // reconstructed from the rows, or the declared type was unrecognised.
      inferred: columnsInferred || !known,
    };
  });

  // A column declared but present in no row is not an error — the producer may
  // be describing a shape it could not fill — but it renders as a column of
  // dashes, which looks like a bug unless we say otherwise.
  const empty = columns.filter(
    (c) => !rows.some((r) => r.cells[c.key] !== undefined && r.cells[c.key] !== null),
  );
  if (empty.length > 0 && rows.length > 0) {
    warnings.push({ code: "emptyColumns", columns: empty.map((c) => c.label) });
  }

  return {
    id: props.id ?? "",
    title: props.title ?? "",
    subtitle: props.subtitle ?? "",
    notes: props.notes ?? "",
    columns,
    rows,
    warnings,
    uncited: rows.length > 0 && rows.every((r) => !r.cite_url),
  };
}
