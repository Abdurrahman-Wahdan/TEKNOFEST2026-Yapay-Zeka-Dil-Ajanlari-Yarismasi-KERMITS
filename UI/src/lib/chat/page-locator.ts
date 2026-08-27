/**
 * Where on the page a piece of context came from.
 *
 * A path alone is nearly useless. "This came from /urunler" leaves the agent to
 * guess, while "the İŞLEYIŞ SÜRECI cell of the Vakıf Katılım Bankası row of the
 * Teverruk finansmanı table" is the difference between answering the question and
 * describing a sentence. All of it is already in the DOM at the moment of the
 * click -- nothing needs wiring per page.
 *
 * Split deliberately: the decisions are pure functions and tested, and the DOM
 * reading is the thin `describeLocation` at the bottom. `npm test` has no DOM, so
 * anything that matters has to be on the pure side of that line.
 */

// Type-only, and deliberately a cycle: `types.ts` imports `ContextLocation` from
// here. Type imports are erased, so nothing circular survives to runtime.
import type { ContextKind } from "./types.ts";

/** A place on the page, as far as it could be worked out. */
export type ContextLocation = {
  /** Locale-stripped pathname. */
  path: string;
  /** The page's own title, from `[data-page-title]`. */
  page?: string;
  /** The nearest heading above it -- usually the card or table title. */
  section?: string;
  /** The table's title, when it is inside one that names itself. */
  table?: string;
  /**
   * What that table is for, in the producer's own words.
   *
   * The single most useful thing to send with a quoted cell: "Katılım bankalarının
   * fatura ödeme talimatı karşılığında sunduğu üyelik hediyelerini kıyaslar" tells
   * the agent what is being compared, so it can answer instead of asking the user
   * what they are looking at.
   */
  about?: string;
  /** Which row, named by the cell that identifies it. */
  row?: string;
  /** Which column, from the header at the same index. */
  column?: string;
  /** What sort of thing it is. */
  kind?: "cell" | "heading" | "link" | "listitem" | "text";
};

/**
 * The column a cell sits under.
 *
 * The last header row, not the first: a grouped header spans categories across the
 * top row and puts the real column names underneath, so reading row zero would
 * report "KONUT" where the answer is "Aylık taksit".
 *
 * `colSpan` is honoured because a grouped header's cells do not line up
 * one-to-one with the body's, and an off-by-one column label is worse than none --
 * it would attribute a figure to the wrong heading.
 */
export function columnForCellIndex(
  headerRows: { text: string; colSpan: number }[][],
  cellIndex: number,
): string | undefined {
  const row = headerRows.at(-1);
  if (!row) return undefined;
  let start = 0;
  for (const cell of row) {
    const span = Math.max(1, cell.colSpan);
    if (cellIndex >= start && cellIndex < start + span) {
      return cell.text.trim() || undefined;
    }
    start += span;
  }
  return undefined;
}

/**
 * Which cell names a row.
 *
 * The first non-empty one, which on every table in this app is the bank. Not the
 * selected cell: "2,89%" identifies nothing, and a row has to be named by
 * something a reader would recognise.
 */
export function rowLabelFromCells(cells: string[]): string | undefined {
  for (const cell of cells) {
    const text = cell.trim();
    // A dash is the table's way of saying "absent", so it names nothing.
    if (text && text !== "—") return text;
  }
  return undefined;
}

/** Everything known about a place, as one readable line. */
export function formatLocation(loc: ContextLocation): string {
  const parts = [
    loc.page ?? loc.path,
    loc.table ?? loc.section,
    loc.row ? `row “${loc.row}”` : undefined,
    loc.column ? `column “${loc.column}”` : undefined,
  ].filter((p): p is string => Boolean(p));
  return parts.join(" › ");
}

/**
 * The short form, for a chip with ~160px to say where something came from.
 *
 * The most specific thing known, not the breadcrumb: on a chip the useful half is
 * the end of the trail, and the page name is already on screen behind it.
 *
 * `kind` matters because the chip's *label* already names the item, and the
 * subline has to add something. An attached row is labelled "Ziraat Katılım
 * Bankası", so the most specific coordinate is the row -- and the chip read
 * "Ziraat Katılım Bankası · Ziraat Katılım Bankası", which says nothing twice.
 * For a row, the useful subline is the table it came out of; for a whole table,
 * the page. A quote is the one kind whose label is its text rather than its
 * position, so a quote keeps the full specificity.
 */
export function shortLocation(loc: ContextLocation, kind?: ContextKind): string {
  const candidates =
    kind === "row"
      ? [loc.table, loc.section, loc.page, loc.path]
      : kind === "table" || kind === "chart"
        ? [loc.section, loc.page, loc.path]
        : [loc.column, loc.row, loc.table, loc.section, loc.page, loc.path];
  return candidates.find((c): c is string => Boolean(c)) ?? loc.path;
}

/**
 * A table's header rows, as text plus spans.
 *
 * Exported so the page outline can reuse the same reading of the same markup.
 */
export function readHeaderRows(
  table: HTMLTableElement,
): { text: string; colSpan: number }[][] {
  return Array.from(table.tHead?.rows ?? []).map((row) =>
    Array.from(row.cells).map((cell) => ({
      text: headerText(cell),
      colSpan: cell.colSpan,
    })),
  );
}

/**
 * A header's name, without the sort marker sitting inside it.
 *
 * The active column renders its direction as a glyph or a phrase in the same
 * button -- so reading the cell whole gave "INSTALMENT▲", and on a text column
 * "PRODUCT A–Z". Neither is the column's name, and both would be handed to the
 * agent as one.
 *
 * Cloned rather than filtered in place: this runs while the user is looking at
 * the table, and removing a node from the live DOM to read around it would take
 * the marker off the screen.
 */
function headerText(cell: HTMLTableCellElement): string {
  if (!cell.querySelector("[data-sort-hint]")) return cell.textContent ?? "";
  const clone = cell.cloneNode(true) as HTMLElement;
  for (const hint of Array.from(clone.querySelectorAll("[data-sort-hint]"))) {
    hint.remove();
  }
  return clone.textContent ?? "";
}

/**
 * The nearest heading above an element.
 *
 * Document order rather than DOM ancestry: a table's title is a sibling above it,
 * not a parent, so `closest()` would never find it. `root` keeps the search inside
 * the page -- without it the drawer's own headings win everywhere, because they
 * come first in the document.
 */
function nearestHeadingAbove(el: Element, root: Element): string | undefined {
  const headings = Array.from(
    root.querySelectorAll("h1, h2, h3, h4, h5, h6, [data-section-title]"),
  );
  let best: Element | undefined;
  for (const heading of headings) {
    // True when `el` comes after `heading` in document order.
    if (heading.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
      best = heading;
    }
  }
  const text = best?.textContent?.trim();
  return text || undefined;
}

/** One cell of a table row, named by its column. */
export type RowCell = { column: string; value: string };

/**
 * What a cell is worth as text when all we have is its markup.
 *
 * A citation cell renders the words "Kaynak" over a URL, deliberately -- a bare
 * domain reads as the bank's front page. Read from the DOM that gives the agent
 * "Open the related page", which cites nothing and cannot be followed. When the
 * cell *is* the link, the address is the datum, which is the same call
 * `cellDisplayText` makes for a `link` column; this is the DOM-side half of it.
 *
 * Only when the anchor is the whole cell. A sentence containing a link is a
 * sentence, and replacing it with a URL would throw the sentence away.
 */
export function cellValueFromMarkup(
  text: string,
  anchors: { href: string; text: string }[],
): string {
  const trimmed = text.trim();
  if (anchors.length === 1 && anchors[0].text.trim() === trimmed && anchors[0].href) {
    return anchors[0].href;
  }
  return trimmed;
}

/**
 * The row an element sits in, as column/value pairs.
 *
 * A lone "%2,95" is very nearly meaningless -- it does not say which bank, which
 * product, or what the instalment beside it is -- so a quoted cell travels with
 * the row around it. Read separately from `describeLocation` rather than bolted
 * onto it: this is body *content*, while a location is a set of coordinates, and
 * only some callers want to pay for it.
 */
export function readRowCells(node: Node | null): RowCell[] | undefined {
  const el = elementOf(node);
  const cell = el?.closest("td, th") as HTMLTableCellElement | null;
  const row = cell?.closest("tr");
  const table = cell?.closest("table") as HTMLTableElement | null;
  if (!cell || !row || !table) return undefined;

  const headers = readHeaderRows(table);
  const cells = Array.from(row.cells).map((c, index) => ({
    column: columnForCellIndex(headers, index) ?? `Column ${index + 1}`,
    value: cellValueFromMarkup(
      c.textContent ?? "",
      Array.from(c.querySelectorAll("a[href]")).map((a) => ({
        href: a.getAttribute("href") ?? "",
        text: a.textContent ?? "",
      })),
    ),
  }));
  return cells.length > 0 ? cells : undefined;
}

/** The nearest element to a node, which may itself be a text node. */
export function elementOf(node: Node | null): Element | null {
  if (!node) return null;
  return node.nodeType === Node.ELEMENT_NODE
    ? (node as Element)
    : (node.parentElement ?? null);
}

/**
 * Work out where an element is. The one DOM-reading function here.
 *
 * Every field is optional and every lookup degrades to `undefined`: this runs on
 * arbitrary markup, and a locator that threw would take the reply button down
 * with it.
 */
export function describeLocation(node: Node | null, path: string): ContextLocation {
  const loc: ContextLocation = { path };
  const el = elementOf(node);
  if (!el) return loc;

  const root = (el.closest("[data-page-root]") as Element | null) ?? document.body;

  loc.page = document.querySelector("[data-page-title]")?.textContent?.trim() || undefined;
  loc.section = nearestHeadingAbove(el, root);

  const cell = el.closest("td, th") as HTMLTableCellElement | null;
  if (cell) {
    loc.kind = "cell";
    const table = cell.closest("table") as HTMLTableElement | null;
    if (table) {
      // The table's own declaration first, then a `<caption>`. Both are things
      // the table said about itself; neither is a guess from nearby markup.
      const declared = table.closest("[data-table-title]") as HTMLElement | null;
      loc.table =
        declared?.dataset.tableTitle || table.caption?.textContent?.trim() || undefined;
      loc.about =
        (table.closest("[data-table-about]") as HTMLElement | null)?.dataset.tableAbout ||
        undefined;
      loc.column = columnForCellIndex(readHeaderRows(table), cell.cellIndex);
    }
    const row = cell.closest("tr");
    if (row) {
      loc.row = rowLabelFromCells(Array.from(row.cells).map((c) => c.textContent ?? ""));
    }
    // A header cell *is* its column, so naming a row for it would be nonsense.
    if (cell.tagName === "TH") {
      loc.column = cell.textContent?.trim() || loc.column;
      loc.row = undefined;
    }
    return loc;
  }

  if (el.closest("h1, h2, h3, h4, h5, h6")) loc.kind = "heading";
  else if (el.closest("a[href]")) loc.kind = "link";
  else if (el.closest("li")) loc.kind = "listitem";
  else loc.kind = "text";

  return loc;
}
