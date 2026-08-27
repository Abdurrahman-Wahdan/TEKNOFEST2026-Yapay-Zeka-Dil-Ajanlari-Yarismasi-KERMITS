"use client";

import { useTranslations } from "next-intl";

import { MultiSelect } from "@/components/ui/MultiSelect";
import { VuiBox } from "@/components/vision";
import type { ResolvedColumn, Row } from "@/lib/contract";
import { distinctValues, filterKind, type FilterState } from "@/lib/table-filter";

/**
 * Filters built entirely from the table's own columns.
 *
 * There is no per-page filter code anywhere in the app, and there must not be:
 * the producer decides what columns exist, so anything hardcoded here would be
 * wrong for the next table it sends. A bank column gets a tick-list because it
 * is a bank column, not because this is the Finansman page.
 *
 * The tick-list is `MultiSelect` — the same component `BoardFilters` puts on
 * the FX board, not a second one that looks like it. This file used to define
 * its own `SelectFilter`, and the two drifted exactly the way `MultiSelect`'s
 * own doc comment warns they will: the label sat inside the button instead of
 * above it, the trigger counted differently, and the select-all row rewrote
 * itself to "Clear selection" on a board where the same row said "All". One
 * component means one set of those decisions.
 */
export function TableFilters({
  columns,
  rows,
  state,
  onChange,
  bankLabels,
}: {
  columns: ResolvedColumn[];
  /** The unfiltered rows — the tick-lists must offer values a filter hid. */
  rows: Row[];
  state: FilterState;
  onChange: (next: FilterState) => void;
  /**
   * Display names for a `bank` column's values.
   *
   * A bank cell holds the provider's own key (`kuveytturk`) and the table draws
   * *Kuveyt Türk*, so a tick-list built from the raw cell text offers a list of
   * keys nobody recognises next to a table of names. The same lookup `sortRows`
   * already takes for the same reason. Optional: a table with no bank column
   * never needs it.
   */
  bankLabels?: Record<string, string>;
  /**
   * Still passed by `TableWidget`, deliberately not destructured: the row count
   * they feed is unmounted (see below), and keeping them on the contract is
   * what makes re-mounting it a one-line change rather than a re-wiring.
   */
  matched: number;
  total: number;
}) {
  const t = useTranslations("components");

  const selects = columns.filter((c) => filterKind(c, rows) === "select");

  return (
    <VuiBox display="flex" flexWrap="wrap" gap="12px" alignItems="flex-end">
      {/* The free-text search is unmounted, not deleted: `FilterState.search`
          and the matching branch in `applyFilters` (with its Turkish folding
          and its tests) are all still here, so re-mounting is putting a
          VuiInput back in this slot. The per-column filters cover the same
          ground with less ambiguity about what is being matched. */}

      {selects.map((column) => {
        const options = distinctValues(rows, column.key);
        // Untouched means everything, and untouched is the key being absent
        // rather than an empty array stored under it — the same distinction
        // the FX board draws, and for the same reason: an empty array is a
        // real selection (nothing ticked) that has to survive being read
        // back, or the select-all toggle reads as stuck on "All".
        const chosen = state.values[column.key];
        return (
          <MultiSelect
            key={column.key}
            label={column.label}
            options={options.map((v) => ({
              value: v,
              label: column.type === "bank" ? (bankLabels?.[v] ?? v) : v,
            }))}
            selected={chosen !== undefined ? chosen : options}
            allLabel={t("selectAll")}
            allSelectedLabel={t("allSelected")}
            onChange={(next) =>
              onChange({ ...state, values: { ...state.values, [column.key]: next } })
            }
          />
        );
      })}

      {/* The numeric min/max filters and the column-visibility toggle are
          unmounted, not deleted: `FilterState.ranges` and `.hidden` are still
          on the type, `applyFilters` still honours them, and `TableWidget`
          still hides what `.hidden` lists. Neither earned its place next to
          the tick-lists — a mile rate is read per row, not bracketed by a
          range, and a column nobody asked to hide is a control that only ever
          takes something away. Re-mounting either is a component in this row.

          The "Clear filters" link goes with them, for the same reason the FX
          board never had one: each tick-list already carries its own select-
          all/clear row, so a second, separate clear is a second answer to the
          question of what is currently in force. */}
    </VuiBox>
  );
}
