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
 * Untouched means everything, in all three -- a filter nobody has touched
 * should not be doing anything. Untouched is the key being absent from
 * `state.values`, not an empty array stored there: an empty array is a real
 * selection (nothing ticked) and has to survive being read back, the same
 * way `BankPicker`'s does on every other page in this app.
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

  // Same `undefined`-vs-`[]` distinction as bank and side, below -- collapsing
  // a full re-selection back down to `[]` made this toggle stuck the same
  // way. The row filter (`applyFilters`, shared with every AI-produced
  // table) already treats an empty `instrument` as "no filter" regardless of
  // how it got that way, so this only fixes the button's own round trip, not
  // which rows show.
  const chosenPairs = state.values.instrument;
  const shownPairs = chosenPairs !== undefined ? chosenPairs : pairs;

  // Bank and side are stored, not read back out of `hidden`.
  //
  // They used to be derived from which columns were hidden, and the two
  // dimensions multiply: turning off both sides hid every column, which made
  // every bank read as "off" too, and from there picking a bank re-applied an
  // empty side list and hid everything again. There was no way back.
  //
  // Stored separately they cannot erase each other. Untouched (the key is
  // missing) means "all" in both — a filter nobody has set should never be
  // the one hiding everything. That has to be `undefined`, not `[]`: this
  // used to collapse a full selection back down to `[]` on write, same value
  // as "untouched", which made deselecting everything indistinguishable from
  // never having chosen — the button read "All" again the instant it was
  // cleared, so the select-all toggle that works everywhere else in the app
  // read as stuck here. `state.values[key]` is now stored exactly as ticked,
  // empty array included, so a real "nothing chosen" survives.
  const chosenBanks = state.values[BANK_KEY];
  const chosenSides = state.values[SIDE_KEY];
  const shownBanks = chosenBanks !== undefined ? chosenBanks : banks;
  const shownSides = chosenSides !== undefined ? chosenSides : SIDES;

  const set = (key: string, next: string[]) =>
    onChange({
      ...state,
      values: { ...state.values, [key]: next },
    });

  return (
    <VuiBox display="flex" flexWrap="wrap" gap="12px" alignItems="flex-end">
      <MultiSelect
        label={t("instrument")}
        options={pairs.map((p) => ({ value: p, label: p }))}
        selected={shownPairs}
        allLabel={t("allPairs")}
        allSelectedLabel={t("allSelected")}
        onChange={(next) => set("instrument", next)}
      />

      <MultiSelect
        label={t("bank")}
        options={banks.map((b) => ({ value: b, label: bankLabels[b] ?? b }))}
        selected={shownBanks}
        allLabel={t("allBanks")}
        allSelectedLabel={t("allSelected")}
        onChange={(next) => set(BANK_KEY, next)}
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
        onChange={(next) => set(SIDE_KEY, next)}
      />
    </VuiBox>
  );
}
