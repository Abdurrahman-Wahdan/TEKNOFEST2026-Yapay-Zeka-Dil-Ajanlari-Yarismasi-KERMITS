"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";
import { resolveTable, type TableProps } from "@/lib/contract";
import {
  EMPTY_FILTERS,
  applyFilters,
  sortRows,
  type FilterState,
  type SortState,
} from "@/lib/table-filter";

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
  const [sort, setSort] = useState<SortState | null>(null);

  // Display names for bank keys. Shares the cache with every other consumer of
  // GET /api/banks, so several tables on a page cost one request. A failure
  // here is not the table's problem: keys render raw and everything else works.
  const { data: banks } = useQuery({ queryKey: ["banks"], queryFn: api.banks });
  const bankLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const bank of banks ?? []) map[bank.name] = bank.display_name;
    return map;
  }, [banks]);

  const table = useMemo(() => resolveTable(props), [props]);

  const visible = useMemo(
    () => table.columns.filter((c) => !filters.hidden.includes(c.key)),
    [table.columns, filters.hidden],
  );

  const rows = useMemo(() => {
    const matched = applyFilters(table.rows, table.columns, filters, locale);
    return sortRows(matched, sort, table.columns, locale);
  }, [table.rows, table.columns, filters, sort, locale]);

  const toggleSort = (key: string) =>
    setSort((current) =>
      current?.key === key
        ? current.direction === "asc"
          ? { key, direction: "desc" }
          : // Third click clears it, so a user can always get back to the
            // producer's original ordering — which is itself information.
            null
        : { key, direction: "asc" },
    );

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

      <VuiBox
        sx={{
          "& .MuiTableRow-root:hover td": {
            background: "rgba(255, 255, 255, 0.03)",
          },
        }}
      >
        <ProducedTable
          columns={visible}
          rows={rows}
          sort={sort}
          onSort={toggleSort}
          bankLabels={bankLabels}
          emptyLabel={table.rows.length === 0 ? t("tableEmpty") : t("noRowsMatch")}
        />
      </VuiBox>

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
