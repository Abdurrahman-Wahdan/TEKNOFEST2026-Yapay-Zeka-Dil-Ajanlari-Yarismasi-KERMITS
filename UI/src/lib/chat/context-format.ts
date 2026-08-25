/**
 * Turning a piece of the UI into something the agent reads well.
 *
 * The format is not a matter of taste. Benchmarked across eleven encodings, a
 * markdown key/value list leads at ~61% answer accuracy and a markdown table
 * reaches ~52%, while CSV comes last at ~44% -- so the obvious "it's tabular,
 * send a spreadsheet" instinct is the worst available choice, and markdown wins
 * twice over by also costing far fewer tokens than HTML. Hence: one record goes
 * out as key/value, many records as a GFM table, and an oversized table is
 * *truncated with the truncation stated* rather than re-encoded.
 *
 * The envelope is an XML-ish tag rather than more markdown. Delimiting attached
 * context with tags is what keeps "the table says 42" apart from the user's own
 * words, and XML scores second only to markdown-KV at being understood.
 *
 * Everything here is pure, which is the point: `npm test` runs `.ts` only, with
 * no DOM, so all the judgement lives in this file and the React layer above it
 * is glue.
 */

import { BLANK_CELL, cellDisplayText, isBlankCell } from "../cell-display.ts";
import type { ResolvedColumn, Row } from "../contract.ts";
import { formatLocation } from "./page-locator.ts";
import type { RowCell } from "./page-locator.ts";
import type { AttachedContext } from "./types.ts";

/**
 * Nothing here truncates.
 *
 * There were row and character caps, and they were a mistake: a table cut to 25 of
 * 30 rows cannot answer "which bank is cheapest", so the agent has to ask a
 * follow-up -- the exact thing attaching a table is meant to remove. Half a table
 * does not save tokens, it wastes a turn.
 *
 * The budget supports it. Gemma 4 carries 128k tokens on the small variants and
 * 256k on 12B and up, while the producer contract caps a table at `MAX_ROWS` (500)
 * and a page at `MAX_COMPONENTS` (8) -- so the worst case the app can even build is
 * a fraction of the smallest window.
 *
 * The deliberate consequence: if a payload ever does exceed what the model can
 * take, the request fails visibly instead of quietly answering from part of the
 * data. That is the right way round.
 */



/** How long a chip label may be before it is elided. */
const MAX_LABEL_CHARS = 48;

export type SerialiseOptions = {
  columns: ResolvedColumn[];
  locale: "tr" | "en";
  bankLabels?: Record<string, string>;
  /** Optional visual header groups, expanded into each leaf column for text. */
  groups?: { key: string; label: string; span: number }[];
};

/**
 * Give every serialised leaf column its full visible identity.
 *
 * The FX board draws a bank name in a grouped header above its BUY/SELL pair.
 * Markdown has no colspan, so sending only the leaf labels produced a table of
 * repeated BUY/SELL columns with no bank attribution. Expanding the group into
 * each leaf makes the text contract unambiguous: "Kuveyt Türk — SELL".
 */
function columnLabels(opts: SerialiseOptions): string[] {
  if (!opts.groups?.length) return opts.columns.map((column) => column.label);

  const groupForColumn = opts.groups.flatMap((group) =>
    Array.from({ length: group.span }, () => group.label),
  );
  // A malformed span row must not shift labels onto the wrong figures. The
  // rendered table requires an exact span total too; fall back to leaf labels
  // rather than inventing an attribution when the contract is inconsistent.
  if (groupForColumn.length !== opts.columns.length) {
    return opts.columns.map((column) => column.label);
  }

  return opts.columns.map((column, index) => {
    const group = groupForColumn[index]?.trim();
    return group ? `${group} — ${column.label}` : column.label;
  });
}

/**
 * A cell, safe to drop inside a markdown table.
 *
 * A pipe in a value ends the column early and shifts every cell after it, so a
 * bank note reading "3 ay | 6 ay" would silently corrupt the row it is in --
 * the agent would read a table whose columns no longer line up with its headers.
 */
function escapeCell(text: string): string {
  return text.replace(/\|/g, "\\|").replace(/\r?\n/g, " ").trim();
}

function cell(row: Row, column: ResolvedColumn, opts: SerialiseOptions): string {
  return escapeCell(cellDisplayText(row.cells[column.key], column, opts.locale, opts.bankLabels));
}

/**
 * One row, as a key/value list -- the strongest format measured for a single
 * record, and the natural one: a row read aloud is "this bank, that rate".
 *
 * Blank cells are kept as their dash rather than dropped. The table shows `—`,
 * and an agent that never sees the field cannot tell "no profit share offered"
 * from "this table has no such column" -- which is the difference between
 * answering and inventing.
 */
export function rowToMarkdownKv(row: Row, opts: SerialiseOptions): string {
  const labels = columnLabels(opts);
  const lines = opts.columns.map(
    (column, index) => `- **${escapeCell(labels[index] ?? column.label)}**: ${cell(row, column, opts)}`,
  );
  if (row.cite_url) lines.push(`- **cite_url**: ${row.cite_url}`);
  if (row.cite_note) lines.push(`- **cite_note**: ${escapeCell(row.cite_note)}`);
  return lines.join("\n");
}

/**
 * Many rows, as a GFM table. Every row, in the order they are on screen.
 */
export function tableToMarkdown(rows: Row[], opts: SerialiseOptions): string {
  const header = `| ${columnLabels(opts).map(escapeCell).join(" | ")} |`;
  // GFM carries alignment in the delimiter row, and the columns already know
  // theirs -- so a money column arrives right-aligned, as it looks on screen.
  const rule = `| ${opts.columns
    .map((c) => (c.align === "right" ? "---:" : c.align === "center" ? ":---:" : "---"))
    .join(" | ")} |`;

  const lines = [header, rule];
  for (const row of rows) {
    lines.push(`| ${opts.columns.map((c) => cell(row, c, opts)).join(" | ")} |`);
  }
  return lines.join("\n");
}

/**
 * A selection, tidied into a quotable line.
 *
 * Browsers hand back whatever whitespace the markup happened to contain -- a
 * selection across table cells arrives full of newlines and runs of spaces -- and
 * none of that is what the user thinks they selected. Normalising, not shortening:
 * the length is the user's choice, and a quote is bounded by what they highlighted.
 */
export function normaliseQuote(raw: string): string {
  return raw.replace(/\s+/g, " ").trim();
}

/**
 * The row around a quoted cell, as a key/value list.
 *
 * Key/value rather than a one-row table: it is the strongest format measured for
 * a single record, and a table with one body row spends three lines on chrome.
 * The cell the user selected is marked, so the agent knows which figure is the
 * question and which are the context around it.
 */
export function formatSurroundingRow(cells: RowCell[], selectedColumn?: string): string {
  return cells
    .map((cell) => {
      const value = escapeCell(cell.value) || BLANK_CELL;
      const mark = selectedColumn && cell.column === selectedColumn ? " ←" : "";
      return `- **${escapeCell(cell.column)}**: ${value}${mark}`;
    })
    .join("\n");
}

/** Shorten a label for a chip, on a word boundary where possible. */
export function elideLabel(text: string, max = MAX_LABEL_CHARS): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  if (collapsed.length <= max) return collapsed;
  const cut = collapsed.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/**
 * What to call an attached row.
 *
 * Prefers the cell that identifies it to a reader -- the bank, or whatever the
 * first readable column is -- because "Kuveyt Türk" is a chip you recognise and
 * "Row 3" is one you have to go back and check. `fallback` arrives already
 * translated; this module holds no strings.
 */
export function rowContextLabel(row: Row, opts: SerialiseOptions, fallback: string): string {
  const preferred =
    opts.columns.find((c) => c.type === "bank") ??
    opts.columns.find((c) => c.type === "text" || c.type === "badge");
  if (preferred && !isBlankCell(row.cells[preferred.key])) {
    return elideLabel(cell(row, preferred, opts));
  }
  return fallback;
}

/**
 * The exact string the agent is given for one attachment.
 *
 * Tagged, so attached data cannot be mistaken for the user's own sentence, and
 * carrying its provenance: an answer about a rate is worth nothing if nobody can
 * say which page and which table it came from.
 */
export function contextToPromptBlock(ctx: AttachedContext): string {
  /**
   * A page snapshot brings its own envelope, so it is passed through untouched.
   *
   * `outlineToMarkdown` already returns `<page-snapshot path=… page=…>…`, and
   * wrapping that in `<attached-context kind="page">` produced a tag inside a tag
   * saying the same thing twice. Worse, the agent's own `look_at_page` puts the
   * identical outline straight into the prose unwrapped -- so the same content
   * reached the model in two different shapes depending on whether the user
   * pressed the eye or the model asked. One envelope per thing, one shape either
   * way.
   */
  if (ctx.kind === "page") return ctx.body;

  const { location: loc } = ctx;
  const attrs = [`kind="${ctx.kind}"`, `label="${attr(ctx.label)}"`, `page="${attr(loc.page ?? loc.path)}"`];
  // One attribute per known coordinate, most general first, so the agent can read
  // "which table, which row, which column" without parsing a sentence. Each is
  // omitted when unknown rather than emitted empty -- `row=""` reads as a fact.
  if (loc.path && loc.page) attrs.push(`path="${attr(loc.path)}"`);
  if (loc.section && loc.section !== loc.table) attrs.push(`section="${attr(loc.section)}"`);
  if (loc.table) attrs.push(`table="${attr(loc.table)}"`);
  if (loc.about) attrs.push(`about="${attr(loc.about)}"`);
  if (loc.row) attrs.push(`row="${attr(loc.row)}"`);
  if (loc.column) attrs.push(`column="${attr(loc.column)}"`);
  if (loc.kind) attrs.push(`element="${loc.kind}"`);
  return `<attached-context ${attrs.join(" ")}>\n${ctx.body}\n</attached-context>`;
}

/** An attribute value that cannot end the attribute or the tag. */
function attr(value: string): string {
  return value.replace(/"/g, "'").replace(/[<>]/g, "");
}

/** The printed breadcrumb, for anywhere a person reads it. */
export { formatLocation };

/** Every attachment for a turn, in one block. */
export function contextBundle(contexts: AttachedContext[]): string {
  return contexts.map(contextToPromptBlock).join("\n\n");
}

export { BLANK_CELL };
