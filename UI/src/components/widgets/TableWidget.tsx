"use client";

import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";
import { resolveTable, type TableProps } from "@/lib/contract";
import {
  EMPTY_FILTERS,
  applyFilters,
  sortRows,
  type FilterState,
} from "@/lib/table-filter";
import { useBankLabels } from "@/lib/use-bank-labels";
import { useTableSort } from "@/lib/use-table-sort";

import { useAttachTable } from "@/lib/chat/use-attach-table";
import { ProducedTable } from "./ProducedTable";
import { TableFilters } from "./TableFilters";

/**
 * One AI-produced table, with its filters.
 *
 * Holds the filter and sort state for a single table. Mounted with a `key` of
 * the table's id by whatever renders it, so switching tables starts clean —
 * carrying a bank filter across to a table with no bank column would silently
 * hide rows for a reason the user could no longer see.
 */
export function TableWidget(props: TableProps) {
  const t = useTranslations("components");
  const tw = useTranslations("components.warning");
  const locale = useLocale() as "tr" | "en";

  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);

  // `TableWidget` is mounted with the table's id as its `key`, so switching
  // tables remounts it and the sort resets for free -- no `resetSort` here.
  const { sort, toggleSort } = useTableSort();
  const bankLabels = useBankLabels();

  const table = useMemo(() => resolveTable(props), [props]);

  const visible = useMemo(
    () => table.columns.filter((c) => !filters.hidden.includes(c.key)),
    [table.columns, filters.hidden],
  );

  const rows = useMemo(() => {
    const matched = applyFilters(table.rows, table.columns, filters, locale);
    return sortRows(matched, sort, table.columns, locale, bankLabels);
  }, [table.rows, table.columns, filters, sort, locale, bankLabels]);

  // The filtered, sorted, visible rows -- what the user is actually looking at.
  const attach = useAttachTable({
    columns: visible,
    rows,
    title: table.title || undefined,
    about: [table.subtitle, table.notes].filter(Boolean).join(" — ") || undefined,
    bankLabels,
  });

  return (
    <VuiBox>
      {/* The subtitle is unmounted, not dropped from the contract: producers may
          still send `subtitle` and `resolveTable` still resolves it, so bringing
          it back is a matter of rendering `table.subtitle` here again. The card
          heading already names the table, and a second line under it repeated
          what the columns say. */}

      <TableFilters
        columns={table.columns}
        rows={table.rows}
        state={filters}
        onChange={setFilters}
        matched={rows.length}
        total={table.rows.length}
      />

      {/* No hover wrapper here any more: row hover is `ProducedTable`'s own,
          so every table in the app gets it and no call site can forget it. */}
      <ProducedTable
        columns={visible}
        rows={rows}
        sort={sort}
        onSort={toggleSort}
        bankLabels={bankLabels}
        emptyLabel={table.rows.length === 0 ? t("tableEmpty") : t("noRowsMatch")}
        title={table.title || undefined}
        about={[table.subtitle, table.notes].filter(Boolean).join(" — ") || undefined}
        onAttachRow={attach.onAttachRow}
        onAttachTable={attach.onAttachTable}
      />

      {table.notes && (
        <VuiBox mt={2}>
          <VuiTypography variant="caption" color="text">
            {table.notes}
          </VuiTypography>
        </VuiBox>
      )}

      {/* Warnings describe the data, not a failure — a column we had to guess
          at, a table we truncated. Quiet, but never hidden: they are the only
          signal that what is on screen is not exactly what arrived. */}
      {table.warnings.length > 0 && (
        <VuiBox mt={1} component="ul" pl={2} sx={{ listStyle: "disc" }}>
          {table.warnings.map((warning) => (
            <VuiBox key={JSON.stringify(warning)} component="li">
              <VuiTypography variant="caption" color="text" opacity={0.7}>
                {warning.code === "truncated"
                  ? tw("truncated", { total: warning.total, shown: warning.shown })
                  : warning.code === "unknownColumnType"
                    ? tw("unknownColumnType", { column: warning.column, type: warning.type })
                    : tw("emptyColumns", { columns: warning.columns.join(", ") })}
              </VuiTypography>
            </VuiBox>
          ))}
        </VuiBox>
      )}
    </VuiBox>
  );
}
