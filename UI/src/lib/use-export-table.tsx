"use client";

import { useCallback, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import {
  ExportDialog,
  type ExportScope,
} from "@/components/ui/ExportDialog";

import type { ResolvedColumn, Row } from "./contract";
import {
  TABLE_FORMATS,
  tablePayload,
  type ExportFormat,
  type ExportRequest,
} from "./export";
import { slugifyTitle } from "./saved-view";

type View = { columns: ResolvedColumn[]; rows: Row[] };

/**
 * The export control every table page passes to `ProducedTable`.
 *
 * One copy for the call sites, exactly as `chat/use-attach-table.ts` is one copy
 * of the attach handlers — and for the same reason. What differs between
 * `/compare`, `/urunler`, `/kampanyalar` and `/ai-overview` is only which rows
 * are in scope; writing the serialisation per page would be four chances for a
 * table to leave the app in a different shape from its neighbour.
 *
 * **It takes two views of the table, and that is the whole point.**
 * `ProducedTable` is handed only what the user can see — filtered rows, the sort
 * they chose, the columns they did not hide. That is the right default for an
 * export, but "give me all 204 rows" is a legitimate second answer, and only the
 * caller holds the unfiltered table to offer it. So both come in here, the
 * dialog offers the choice when they differ, and it stays out of the way when
 * they do not.
 *
 * Returns the dialog as an element rather than props to spread. A hook that
 * hands back `dialogProps` is a hook whose caller can forget to render them, and
 * the symptom is a button that does nothing.
 */
export function useExportTable({
  view,
  full,
  title,
  subtitle = "",
  note = "",
  bankLabels,
}: {
  /** What the user is looking at: filtered, sorted, visible columns. */
  view: View;
  /**
   * Everything the table holds. Pass the same object as `view` where there is
   * no filtering to speak of; the dialog compares the two and drops the choice.
   */
  full: View;
  title?: string;
  /** What the table is for — the producer's own description. */
  subtitle?: string;
  note?: string;
  bankLabels?: Record<string, string>;
}) {
  const t = useTranslations("components");
  const locale = useLocale() as "tr" | "en";
  const [open, setOpen] = useState(false);

  // A table with no title still has to produce a file with a name, and the
  // heading the page already draws for it is the honest one.
  const name = title || t("untitledTable");

  const request = useCallback(
    (format: ExportFormat, scope: ExportScope): ExportRequest => {
      const chosen = scope === "full" ? full : view;
      return {
        format,
        source: {
          kind: "table",
          table: tablePayload({
            columns: chosen.columns,
            rows: chosen.rows,
            title: name,
            subtitle,
            note,
            locale,
            bankLabels,
          }),
        },
      };
    },
    [bankLabels, full, locale, name, note, subtitle, view],
  );

  const counts = useMemo(
    () => ({ view: view.rows.length, full: full.rows.length }),
    [full.rows.length, view.rows.length],
  );

  return {
    onExportTable: useCallback(() => setOpen(true), []),
    dialog: (
      <ExportDialog
        open={open}
        onClose={() => setOpen(false)}
        formats={TABLE_FORMATS}
        request={request}
        scope={counts}
        fallbackName={slugifyTitle(name)}
      />
    ),
  };
}
