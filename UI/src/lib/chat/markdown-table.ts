/**
 * Reading a table out of the agent's answer, so it can be saved.
 *
 * Streamdown hands every overridden element the parsed HAST node alongside the DOM
 * attributes (`node?: Element`, see `components/chat/markdown-dom.ts`). That node is
 * the whole `<table>` -- rows, cells, the text inside them, and the source offsets
 * -- which is what makes this possible without a second markdown parse.
 *
 * Walking the HAST rather than the rendered React children is the reason this is a
 * pure module with tests. `MdTable`'s `children` are already MUI elements: pulling
 * text back out of them means reaching into element internals, and it breaks the
 * moment a cell contains anything but plain text. The HAST is data.
 *
 * The output is `TableProps` -- the same shape `api/saved_tables.py` produces and
 * `TableWidget` renders. The two paths deliberately converge here: a table saved by
 * the agent and the same table saved by hand must land as the same props.
 *
 * **Nothing here truncates.** Every row and every cell goes through whole.
 */

import type { CellValue, Column, Row, TableProps } from "../contract.ts";
import { slugifyTitle } from "../saved-view.ts";

/**
 * The shape of a HAST node, declared structurally rather than imported.
 *
 * The `hast` types are type-only, and this module is tested under
 * `node --experimental-strip-types`, which strips types without resolving them.
 * Naming the three fields actually read keeps the test runner out of the
 * dependency graph -- and this is the whole surface used, so there is nothing to
 * drift.
 */
export type HastNode = {
  type?: string;
  tagName?: string;
  value?: string;
  properties?: { style?: string; [key: string]: unknown } | null;
  position?: { start?: { offset?: number } } | null;
  children?: HastNode[] | null;
};

/** The synthetic source column, matching `CITE_KEY` in `api/saved_tables.py`. */
const CITE_KEY = "kaynak";

/** All the text under a node, concatenated. */
export function hastText(node: HastNode | null | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  // `raw` covers inline HTML that was not parsed into elements.
  if (node.type === "raw") return node.value ?? "";
  const children = node.children ?? [];
  return children.map(hastText).join("");
}

function tagged(node: HastNode | null | undefined, tags: string[]): HastNode[] {
  const out: HastNode[] = [];
  const walk = (current: HastNode | null | undefined) => {
    if (!current) return;
    if (current.tagName && tags.includes(current.tagName)) {
      out.push(current);
      // A `tr` cannot nest inside a `tr`, and a nested table's rows are not this
      // table's rows, so there is no reason to descend further.
      return;
    }
    for (const child of current.children ?? []) walk(child);
  };
  walk(node);
  return out;
}

/** Markdown's `|:---|---:|` arrives as an inline `text-align`. */
function alignOf(node: HastNode): Column["align"] | undefined {
  const style = node.properties?.style;
  if (typeof style !== "string") return undefined;
  const match = /text-align:\s*(left|center|right)/i.exec(style);
  return match ? (match[1].toLowerCase() as Column["align"]) : undefined;
}

function keysFor(labels: string[]): string[] {
  const keys: string[] = [];
  const seen = new Map<string, number>();
  labels.forEach((label, index) => {
    let key = slugifyTitle(label, `col${index + 1}`);
    const count = (seen.get(key) ?? 0) + 1;
    seen.set(key, count);
    // Two columns legitimately share a header -- an FX board has two "Alış".
    // `cells` is a dict, so they have to become distinct keys.
    if (count > 1) key = `${key}-${count}`;
    keys.push(key);
  });
  return keys;
}

/**
 * The `<table>` node as saveable props, or `null` when there is nothing to save.
 *
 * `null` rather than an empty table is what hides the save button: a table with no
 * header and no rows is a half-streamed fragment, and offering to save it is
 * offering to save nothing.
 */
export function tableFromHast(
  node: HastNode | null | undefined,
  options: { title?: string } = {},
): TableProps | null {
  if (!node) return null;

  const rowNodes = tagged(node, ["tr"]);
  if (rowNodes.length === 0) return null;

  // The header is the first row made of `th`. Falling back to the first row
  // matters because `parseIncompleteMarkdown` can hand over a table whose header
  // has not been recognised yet, and a table whose first row becomes data would
  // silently shift every column label by one.
  let headerIndex = rowNodes.findIndex((row) => tagged(row, ["th"]).length > 0);
  if (headerIndex === -1) headerIndex = 0;

  const headerCells = tagged(rowNodes[headerIndex], ["th", "td"]);
  const labels = headerCells.map((cell) => hastText(cell).trim());
  if (labels.length === 0) return null;

  const keys = keysFor(labels);
  const columns: Column[] = keys.map((key, index) => {
    const column: Column = { key, label: labels[index] };
    const align = alignOf(headerCells[index]);
    if (align) column.align = align;
    // No `type`: `inferColumnType` reads the values and decides, which beats
    // guessing from a header label. The one exception is the source column the
    // agent's own tables carry, which is a link and would otherwise render as a
    // bare URL.
    if (key === CITE_KEY) column.type = "link";
    return column;
  });

  const rows: Row[] = [];
  rowNodes.forEach((rowNode, index) => {
    if (index === headerIndex) return;
    const cellNodes = tagged(rowNode, ["td", "th"]);
    if (cellNodes.length === 0) return;
    const cells: Record<string, CellValue> = {};
    cellNodes.forEach((cell, position) => {
      // A row longer than the header keeps its extra cells under a generated key.
      // Dropping them loses data with nothing on screen to say so.
      const key = position < keys.length ? keys[position] : `col${position + 1}`;
      const text = hastText(cell).trim();
      cells[key] = text === "" ? null : text;
    });
    // `null`, not "": the contract reads null as "not found" and renders an em
    // dash, which is what a short row means.
    for (const key of keys.slice(cellNodes.length)) cells[key] = null;
    rows.push({ cells });
  });

  if (rows.length === 0) return null;

  const props: TableProps = { columns, rows };
  if (options.title && options.title.trim() !== "") props.title = options.title.trim();
  return props;
}

/**
 * The nearest markdown heading above a table, as its name.
 *
 * The agent almost always writes `## Konut finansmanı karşılaştırması` and then the
 * table, so this recovers the name the user already read rather than inventing one.
 *
 * `offset` comes from the HAST node's `position`, which `parseIncompleteMarkdown`
 * can strip while a message is still arriving -- hence the `undefined` guard and
 * the caller's fallback chain. Fenced blocks are skipped so a `#` comment inside a
 * code sample cannot become a table's title.
 */
export function headingBefore(
  source: string | undefined,
  offset: number | undefined,
): string | undefined {
  if (!source || typeof offset !== "number" || offset < 0) return undefined;

  let fenced = false;
  let heading: string | undefined;
  let position = 0;

  for (const line of source.slice(0, offset).split("\n")) {
    // Counted rather than skipped-to-close: an unterminated fence at the end of a
    // streaming message must still hide the lines inside it.
    if (/^\s{0,3}(```|~~~)/.test(line)) fenced = !fenced;
    else if (!fenced) {
      const match = /^\s{0,3}(#{1,6})\s+(.*\S)\s*$/.exec(line);
      if (match) heading = match[2].replace(/\s*#+\s*$/, "").trim();
    }
    position += line.length + 1;
    if (position > offset) break;
  }
  return heading && heading !== "" ? heading : undefined;
}

/** A table's own name when nothing else supplied one: its first header plus size. */
export function fallbackTitle(props: TableProps, pattern: string): string {
  const first = props.columns?.[0]?.label;
  const label = typeof first === "string" && first.trim() !== "" ? first.trim() : "";
  return pattern.replace("{label}", label).replace("{count}", String(props.rows.length));
}
