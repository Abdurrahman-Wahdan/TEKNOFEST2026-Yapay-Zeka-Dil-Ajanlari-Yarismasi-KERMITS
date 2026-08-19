/**
 * The page, as text the agent can read.
 *
 * The alternative was a screenshot, and for this app text wins decisively. A rate
 * table read from pixels invites misreading `2,89%` as `289` -- in a finance app
 * that is not a cosmetic error -- while the same table as markdown is exact. It is
 * also one to two percent of the tokens of the real DOM: this is MUI plus emotion
 * plus the Vision template, so the markup is deeply nested with generated class
 * names, and almost all of it is wrapper divs. The image stays available as a
 * separate tool for when the question is genuinely visual.
 *
 * Same split as the locator: the shaping is pure and tested, and `outlinePage` is
 * the thin DOM read at the bottom.
 */

import { cellValueFromMarkup, readHeaderRows } from "./page-locator.ts";

/**
 * Multi-row headers, combined into one name per column.
 *
 * The FX board is the case that forces this: bank names span the top row and
 * BUY/SELL sit underneath, so reading only the bottom row produced
 * `| BUY | SELL | BUY | SELL | ...` fourteen times over -- every rate present and
 * no way to tell whose it was. Combining gives `Kuveyt Türk — BUY`, which is the
 * question people actually ask of that table.
 *
 * Spans are walked per row, because a group header's cells do not line up
 * one-to-one with the columns beneath them.
 */
export function flattenHeaders(
  headerRows: { text: string; colSpan: number }[][],
): string[] {
  if (headerRows.length === 0) return [];
  const width = headerRows[headerRows.length - 1].reduce(
    (n, c) => n + Math.max(1, c.colSpan),
    0,
  );
  const parts: string[][] = Array.from({ length: width }, () => []);

  for (const row of headerRows) {
    let index = 0;
    for (const cell of row) {
      const span = Math.max(1, cell.colSpan);
      const text = cell.text.trim();
      for (let i = index; i < index + span && i < width; i += 1) {
        // Skip blanks and anything already said by an outer row, so a group that
        // repeats its column's name does not read "BUY — BUY".
        if (text && !parts[i].includes(text)) parts[i].push(text);
      }
      index += span;
    }
  }

  return parts.map((p) => p.join(" — "));
}

/**
 * Nothing here truncates.
 *
 * There were caps -- 25 rows per table, 12k characters overall -- and they were a
 * mistake. A 30-row board arriving as 25 rows cannot answer "which is cheapest",
 * so the agent asks a follow-up, which is the exact thing looking at the page is
 * meant to avoid. Half a page does not save tokens, it wastes a turn.
 *
 * Gemma 4 carries 128k tokens on the small variants and 256k on 12B and up, and the
 * producer contract caps a table at `MAX_ROWS` (500) and a page at
 * `MAX_COMPONENTS` (8) -- so the largest page this app can build is a fraction of
 * the smallest window. If a payload ever does exceed it, the request fails visibly
 * rather than the agent quietly answering from part of the page.
 */

/** What the page is showing, in the order a reader meets it. */
export type OutlineNode =
  | { type: "heading"; text: string }
  | { type: "text"; text: string }
  | { type: "control"; label: string; value: string }
  | { type: "table"; title?: string; about?: string; headers: string[]; rows: string[][] };

export type PageOutline = {
  path: string;
  page?: string;
  nodes: OutlineNode[];
};

/** A cell, safe inside a markdown table. */
function escape(text: string): string {
  return text.replace(/\|/g, "\\|").replace(/\r?\n/g, " ").trim();
}

/**
 * The outline as markdown, wrapped so it cannot be mistaken for the user's words.
 *
 * Controls come out as a plain list of label/value pairs, and that list is the most
 * valuable part of the whole snapshot: "which product is selected, what amount, what
 * term" is the state that explains every figure on the page, and it is exactly what
 * a user means by "look at what I'm looking at".
 */
export function outlineToMarkdown(outline: PageOutline): string {
  const lines: string[] = [];
  const push = (line: string) => {
    lines.push(line);
  };

  const controls: OutlineNode[] = [];
  for (const node of outline.nodes) if (node.type === "control") controls.push(node);

  if (controls.length > 0) {
    push("## Current selections");
    for (const control of controls) {
      if (control.type !== "control") continue;
      push(`- **${escape(control.label)}**: ${escape(control.value)}`);
    }
    push("");
  }

  push("## On screen");
  for (const node of outline.nodes) {
    if (node.type === "control") continue;

    if (node.type === "heading") {
      push(`### ${escape(node.text)}`);
      continue;
    }
    if (node.type === "text") {
      push(escape(node.text));
      continue;
    }

    // A table, whole.
    if (node.title) push(`### ${escape(node.title)}`);
    if (node.about) push(`_${escape(node.about)}_`);
    push(`| ${node.headers.map(escape).join(" | ")} |`);
    push(`| ${node.headers.map(() => "---").join(" | ")} |`);
    for (const row of node.rows) push(`| ${row.map(escape).join(" | ")} |`);
    push("");
  }

  const attrs = [`path="${outline.path.replace(/"/g, "'")}"`];
  if (outline.page) attrs.push(`page="${outline.page.replace(/"/g, "'")}"`);

  return `<page-snapshot ${attrs.join(" ")}>\n${lines.join("\n").trim()}\n</page-snapshot>`;
}

/** How many things the snapshot found, for the chip's subline. */
export function outlineSummary(outline: PageOutline): { tables: number; controls: number } {
  let tables = 0;
  let controls = 0;
  for (const node of outline.nodes) {
    if (node.type === "table") tables += 1;
    if (node.type === "control") controls += 1;
  }
  return { tables, controls };
}

/** Text worth carrying: long enough to be prose, short enough not to be a dump. */
const MIN_TEXT = 24;
const MAX_TEXT = 400;

/**
 * Anything that is chrome rather than page content.
 *
 * `select`/`option` are in here for a reason worth stating: an `<option>` is a leaf
 * carrying text, so the walker happily reported all fifteen products in the
 * dropdown as things "on screen" -- when only the selected one is visible, and that
 * one is already named under Current selections. Fifteen lines of noise, and the
 * agent left to guess which was chosen.
 */
const SKIP_TEXT =
  "script, style, svg, select, option, optgroup, [aria-hidden='true'], [data-no-outline]";

/**
 * The same idea for the control walk, minus the form elements themselves.
 *
 * Two lists, because `select` belongs in one and not the other: the text walker
 * must skip a dropdown's options, and the control walker must not skip the dropdown.
 * Sharing one list meant adding `select` to stop the option noise and silently
 * emptying Current selections -- the most useful part of the snapshot.
 */
const SKIP_CONTROL = "script, style, [aria-hidden='true'], [data-no-outline]";

/**
 * A control's visible caption, when nothing associates it with the field.
 *
 * The app's own controls carry `aria-label` now, but this is arbitrary markup: a
 * caption rendered as a box above the field is a common enough pattern that
 * falling back to it is worth more than reporting a nameless control. Bounded to
 * short text so a paragraph next to a field is never mistaken for its label, and
 * skipping anything that contains a control of its own so two fields in a row do
 * not borrow each other's names.
 */
function nearbyCaption(field: Element): string {
  let node: Element | null = field;
  for (let depth = 0; node && depth < 3; depth += 1) {
    let prev: Element | null = node.previousElementSibling;
    while (prev) {
      if (!prev.querySelector("select, input, textarea")) {
        const text = (prev.textContent ?? "").replace(/\s+/g, " ").trim();
        if (text && text.length <= 40) return text;
      }
      prev = prev.previousElementSibling;
    }
    node = node.parentElement;
  }
  return "";
}

/**
 * Read the page. The one DOM-facing function here.
 *
 * Walks `root` in document order so the outline reads in the order the user sees,
 * and takes tables whole rather than as the text of their cells -- a table
 * flattened into prose is the one thing markdown does better than the DOM.
 */
export function outlinePage(root: Element, path: string): PageOutline {
  const nodes: OutlineNode[] = [];
  const page = document.querySelector("[data-page-title]")?.textContent?.trim() || undefined;

  // Controls first, because they are the page's state and the reason its figures
  // say what they say.
  for (const el of Array.from(root.querySelectorAll("select, input, textarea"))) {
    const field = el as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
    if (field.closest(SKIP_CONTROL)) continue;
    if (field instanceof HTMLInputElement && ["hidden", "file"].includes(field.type)) continue;

    const label =
      field.getAttribute("aria-label")?.trim() ||
      (field.labels?.[0]?.textContent ?? "").trim() ||
      field.getAttribute("name")?.trim() ||
      nearbyCaption(field) ||
      field.getAttribute("placeholder")?.trim() ||
      "";
    const value =
      field instanceof HTMLSelectElement
        ? (field.selectedOptions[0]?.textContent ?? "").trim()
        : field instanceof HTMLInputElement && (field.type === "checkbox" || field.type === "radio")
          ? field.checked
            ? "on"
            : "off"
          : field.value.trim();
    if (label && value) nodes.push({ type: "control", label, value });
  }

  const seenTables = new Set<Element>();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
    acceptNode: (node) => {
      const el = node as Element;
      if (el.closest(SKIP_TEXT)) return NodeFilter.FILTER_REJECT;
      if (el.tagName === "TABLE") return NodeFilter.FILTER_ACCEPT;
      // Skip inside a table: it is taken whole above.
      if (el.closest("table")) return NodeFilter.FILTER_REJECT;
      if (/^H[1-6]$/.test(el.tagName)) return NodeFilter.FILTER_ACCEPT;
      // A leaf that carries its own text, rather than every wrapper repeating it.
      if (el.children.length === 0) return NodeFilter.FILTER_ACCEPT;
      return NodeFilter.FILTER_SKIP;
    },
  });

  const seenText = new Set<string>();
  while (walker.nextNode()) {
    const el = walker.currentNode as Element;

    if (el.tagName === "TABLE") {
      const table = el as HTMLTableElement;
      if (seenTables.has(table)) continue;
      seenTables.add(table);
      const headerRows = readHeaderRows(table);
      const headers = flattenHeaders(headerRows);
      const bodyRows = Array.from(table.tBodies).flatMap((b) => Array.from(b.rows));
      /**
       * Which columns are data rather than controls.
       *
       * The attach-a-row buttons live in a trailing column with no heading, and it
       * came through as an empty `|  |` on every row -- a column of nothing for the
       * agent to wonder about. Marked in the markup rather than guessed at by
       * position, so it stays right if the column ever moves.
       */
      const keep = (headerRows.at(-1) ?? []).map((_, index) => {
        const headerCell = table.tHead?.rows[table.tHead.rows.length - 1]?.cells[index];
        return !headerCell?.hasAttribute("data-no-outline");
      });
      const wanted = (index: number) => keep[index] !== false;
      nodes.push({
        type: "table",
        title:
          (table.closest("[data-table-title]") as HTMLElement | null)?.dataset.tableTitle ||
          undefined,
        about:
          (table.closest("[data-table-about]") as HTMLElement | null)?.dataset.tableAbout ||
          undefined,
        headers: headers.filter((_, index) => wanted(index)),
        rows: bodyRows.map((row) =>
          Array.from(row.cells)
            .filter((_, index) => wanted(index))
            .map((cell) =>
            // Same reading as an attached row: a citation cell gives its URL, not
            // the "Kaynak" label, so the agent can follow what it was shown.
            cellValueFromMarkup(
              cell.textContent ?? "",
              Array.from(cell.querySelectorAll("a[href]")).map((a) => ({
                href: a.getAttribute("href") ?? "",
                text: a.textContent ?? "",
              })),
            ),
            ),
        ),
      });
      continue;
    }

    const text = (el.textContent ?? "").replace(/\s+/g, " ").trim();
    if (!text || text.length < MIN_TEXT || text.length > MAX_TEXT) continue;
    // The same string twice is a wrapper and its leaf, not two facts.
    if (seenText.has(text)) continue;
    seenText.add(text);
    nodes.push({
      type: /^H[1-6]$/.test(el.tagName) ? "heading" : "text",
      text,
    });
  }

  return { path, page, nodes };
}
