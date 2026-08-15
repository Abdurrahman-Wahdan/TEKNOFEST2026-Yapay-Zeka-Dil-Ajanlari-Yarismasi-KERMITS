"use client";

import { useTranslations } from "next-intl";

import { MultiSelect } from "@/components/ui/MultiSelect";
import { VuiBox } from "@/components/vision";
import { BANK_KEY, SIDE_KEY, SIDES } from "@/lib/board-filter";
import type { FilterState } from "@/lib/table-filter";

/**
 * The FX board's filters: three, and only three.
 *
 * The general `TableFilters` bar offers a control per column — free text, a
 * tick-list for every categorical column, a min/max for every numeric one, and
 * a visibility toggle each. On an AI table of unknown shape that is the point.
 * On this board it means fourteen controls for a question that is always one
 * of three: which pair, which bank, buying or selling.
 *
 * Each maps onto a different part of the same `FilterState`, which is why they
 * can look alike and behave differently:
 *
 *   pair -> `values.instrument`, filtering rows
 *   bank -> `hidden`, dropping that bank's two columns
 *   side -> `hidden`, dropping every buy or every sell column
 *
 * Empty means everything, in all three. A filter nobody has touched should not
 * be doing anything, and "no ticks" reading as "show nothing" is the way that
 * goes wrong.
 */
export function BoardFilters({
  pairs,
  banks,
  bankLabels,
  state,
  onChange,
}: {
  /** Every pair on the board, filtered or not — a hidden one must stay offerable. */
  pairs: string[];
  banks: string[];
  bankLabels: Record<string, string>;
  state: FilterState;
  onChange: (next: FilterState) => void;
}) {
  const t = useTranslations("comparator");

  const chosenPairs = state.values.instrument ?? [];

  // Bank and side are stored, not read back out of `hidden`.
  //
  // They used to be derived from which columns were hidden, and the two
  // dimensions multiply: turning off both sides hid every column, which made
  // every bank read as "off" too, and from there picking a bank re-applied an
  // empty side list and hid everything again. There was no way back.
  //
  // Stored separately they cannot erase each other, and empty means "all" in
  // both — a filter nobody has set should never be the one hiding everything.
  const chosenBanks = state.values[BANK_KEY] ?? [];
  const chosenSides = state.values[SIDE_KEY] ?? [];
  const shownBanks = chosenBanks.length ? chosenBanks : banks;
  const shownSides = chosenSides.length ? chosenSides : SIDES;

  const set = (key: string, next: string[], all: string[]) =>
    onChange({
      ...state,
      // Everything ticked is the same as no filter. Storing it as one keeps a
      // bank that appears later from being silently excluded.
      values: { ...state.values, [key]: next.length === all.length ? [] : next },
    });

  return (
    <VuiBox display="flex" flexWrap="wrap" gap="12px" alignItems="flex-end">
      <MultiSelect
        label={t("instrument")}
        options={pairs.map((p) => ({ value: p, label: p }))}
        selected={chosenPairs.length ? chosenPairs : pairs}
        allLabel={t("allPairs")}
        allSelectedLabel={t("allSelected")}
        onChange={(next) =>
          onChange({
            ...state,
            // Everything ticked is the same as no filter, and storing it as one
            // keeps a pair that appears later from being silently excluded.
            values: { ...state.values, instrument: next.length === pairs.length ? [] : next },
          })
        }
      />

      <MultiSelect
        label={t("bank")}
        options={banks.map((b) => ({ value: b, label: bankLabels[b] ?? b }))}
        selected={shownBanks}
        allLabel={t("allBanks")}
        allSelectedLabel={t("allSelected")}
        onChange={(next) => set(BANK_KEY, next, banks)}
      />

      <MultiSelect
        label={t("side")}
        options={[
          { value: "buy", label: t("buy") },
          { value: "sell", label: t("sell") },
        ]}
        selected={shownSides}
        allLabel={t("allSides")}
        allSelectedLabel={t("allSelected")}
        onChange={(next) => set(SIDE_KEY, next, SIDES)}
      />
    </VuiBox>
  );
}
