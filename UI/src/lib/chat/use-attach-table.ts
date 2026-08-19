"use client";

import { useLocale, useTranslations } from "next-intl";
import { useCallback, useMemo } from "react";

import { usePathname } from "@/i18n/navigation";
import type { ResolvedColumn, Row } from "@/lib/contract";

import { useChat } from "./ChatProvider";
import {
  elideLabel,
  rowContextLabel,
  rowToMarkdownKv,
  tableToMarkdown,
} from "./context-format";

/**
 * The two attach handlers every table page passes to `ProducedTable`.
 *
 * One copy for three call sites. The serialisation is identical wherever it is
 * done -- what differs between `/compare`, `/urunler` and `/finansman` is only
 * which columns and rows are in scope -- so writing it per page would be three
 * chances for a table to reach the agent in a different shape from its neighbour.
 *
 * Give it the rows the user is *looking at* -- filtered, sorted, visible columns --
 * not the raw payload. Attaching a 200-row table when the user has filtered it to
 * three is attaching something they never saw.
 */
export function useAttachTable({
  columns,
  rows,
  title,
  about,
  bankLabels,
}: {
  columns: ResolvedColumn[];
  rows: Row[];
  /** The table's name, as the page shows it. */
  title?: string;
  /** What the table is for -- the producer's description, or the query behind it. */
  about?: string;
  bankLabels?: Record<string, string>;
}) {
  const t = useTranslations("chat");
  const locale = useLocale() as "tr" | "en";
  const pathname = usePathname();
  const { attachments, setPopupOpen } = useChat();

  const options = useMemo(
    () => ({ columns, locale, bankLabels }),
    [columns, locale, bankLabels],
  );

  /**
   * Where any attachment from this table says it came from.
   *
   * Built here rather than read back out of the DOM: the call site knows the
   * title and the description exactly, and a button press has the data in hand.
   * `describeLocation` exists for the other direction -- a selection, where all
   * we have is the node the user happened to highlight.
   */
  const base = useMemo(
    () => ({ path: pathname, table: title, about }),
    [pathname, title, about],
  );

  /** Open the panel unless the user is already looking at the conversation. */
  const reveal = useCallback(() => {
    if (pathname !== "/chat") setPopupOpen(true);
  }, [pathname, setPopupOpen]);

  const onAttachRow = useCallback(
    (row: Row, index: number) => {
      attachments.addContext({
        kind: "row",
        // Named after the bank, or whatever identifies it, rather than "Row 4":
        // the chip has to be recognisable once four of them are staged. The
        // fallback arrives translated -- `context-format` holds no strings.
        label: rowContextLabel(row, options, t("rowNumber", { number: index + 1 })),
        // Key/value, the strongest format measured for a single record.
        body: rowToMarkdownKv(row, options),
        format: "markdown-kv",
        location: {
          ...base,
          row: rowContextLabel(row, options, t("rowNumber", { number: index + 1 })),
        },
      });
      reveal();
    },
    [attachments, options, base, t, reveal],
  );

  const onAttachTable = useCallback(() => {
    attachments.addContext({
      kind: "table",
      label: elideLabel(title ?? t("thisTable")),
      // Every row, in the order they are on screen. Nothing is cut.
      body: tableToMarkdown(rows, options),
      format: "markdown",
      location: base,
      /** Drives the chip's "12 rows" subline. */
      count: rows.length,
    });
    reveal();
  }, [attachments, rows, options, base, title, t, reveal]);

  return { onAttachRow, onAttachTable };
}
