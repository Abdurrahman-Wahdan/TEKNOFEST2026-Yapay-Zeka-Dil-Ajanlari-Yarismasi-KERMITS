/**
 * Turning what is on screen into the payload the export endpoint takes.
 *
 * The browser sends each cell **twice**: the raw datum and the text it drew.
 * That is not redundancy, it is the split that makes four formats possible from
 * one request. XLSX writes the datum, so a rate column can still be summed and
 * sorted in Excel; PDF and DOCX write the drawn text, so the page reads exactly
 * like the table it came from — `%2,89`, `₺1.234,56`, Turkish decimal comma and
 * all.
 *
 * Formatting happens here rather than in Python for the reason
 * `api/schemas/components.py` already states about prop schemas: the rules live
 * in TypeScript (`cell-display.ts`), and a second copy in another language would
 * drift the first time one side learned a new column type. The screen has
 * already answered "what does this cell say" in order to draw it; the export
 * asks for that answer rather than working it out again.
 *
 * **Nothing here caps anything.** Every row and every column the caller passes
 * goes into the payload whole.
 */

import type { components } from "@/types/api";

import { cellDisplayText, isBlankCell } from "./cell-display.ts";
import type { ResolvedColumn, Row } from "./contract";

type Schemas = components["schemas"];

export type ExportFormat = Schemas["ExportRequest"]["format"];
export type ExportTable = Schemas["ExportTableIn"];
export type ExportRequest = Schemas["ExportRequest"];

/** The order the dialog offers them in: cheapest and most portable first. */
export const TABLE_FORMATS: readonly ExportFormat[] = ["csv", "xlsx", "pdf", "docx"];

/**
 * What a report can become.
 *
 * A report is prose with tables in it, and a CSV of prose is a file nobody can
 * open usefully. The API refuses the other two with a reason (see
 * `api/schemas/export.py`); this list is why the dialog never offers them.
 */
export const REPORT_FORMATS: readonly ExportFormat[] = ["pdf", "docx"];

/**
 * One table, as the request body wants it.
 *
 * Give it the rows the user is *looking at*, or the whole table — that choice
 * belongs to the caller, and it is the only thing the scope toggle changes.
 * The server never learns which it got, because it never has to: there is no
 * filter state on that side to disagree with.
 */
export function tablePayload({
  columns,
  rows,
  title,
  subtitle = "",
  note = "",
  locale,
  bankLabels,
}: {
  columns: ResolvedColumn[];
  rows: Row[];
  title: string;
  subtitle?: string;
  note?: string;
  locale: "tr" | "en";
  bankLabels?: Record<string, string>;
}): ExportTable {
  return {
    title,
    subtitle,
    note,
    columns: columns.map((column) => ({
      key: column.key,
      label: column.label,
      type: column.type,
      align: column.align,
      currency: column.currency,
      decimals: column.decimals ?? null,
    })),
    rows: rows.map((row) => ({
      cite_url: row.cite_url ?? "",
      cite_note: row.cite_note ?? "",
      cells: columns.map((column) => {
        const value = row.cells[column.key];
        return {
          // `undefined` is not a JSON value; a cell the producer omitted has to
          // travel as an explicit null or the column shifts under it.
          value: isBlankCell(value) ? null : (value ?? null),
          display: isBlankCell(value)
            ? ""
            : cellDisplayText(value, column, locale, bankLabels),
          // A `link` cell's href is the value itself. `cellDisplayText` returns
          // the URL for those, but the two are separate fields on the wire
          // because the source column shows one and links to the other.
          href: column.type === "link" && typeof value === "string" ? value : "",
          note: row.cell_notes?.[column.key] ?? "",
          tone: row.cell_tones?.[column.key] ?? "",
        };
      }),
    })),
  };
}

/**
 * Hand a downloaded blob to the browser, under the name the server chose.
 *
 * The anchor has to be in the document before it is clicked — a detached one is
 * a no-op in Firefox — and the object URL has to be revoked afterwards or the
 * blob is pinned in memory for the life of the tab. A user exporting a dozen
 * comparison tables in a sitting is the case that turns into a leak.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** `filename*=UTF-8''…`, falling back to `filename="…"`, falling back to a guess. */
export function filenameFrom(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;

  // RFC 5987 first: it is the one that carries Turkish letters, and every
  // current browser prefers it. `decodeURIComponent` can throw on malformed
  // input, and a bad header must not cost the user their download.
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
      /* fall through to the plain parameter */
    }
  }

  const plain = /filename="([^"]+)"/i.exec(disposition);
  return plain ? plain[1] : fallback;
}
