import type { FilterState } from "./table-filter";

/**
 * Where the board's bank and side choices live inside `FilterState`.
 *
 * Reserved keys rather than real columns: they are one control each acting on
 * many columns, and `values` is already the place a selection is kept. The
 * double underscore keeps them from ever colliding with a column key a bank
 * could produce.
 */
export const BANK_KEY = "__bank";
export const SIDE_KEY = "__side";
export const SIDES = ["buy", "sell"];

/**
 * The columns to hide, given what the user picked.
 *
 * Empty means everything, in both dimensions. That is what stops the two from
 * cancelling each other out: they used to be inferred from the hidden columns,
 * so clearing one made the other look cleared too and nothing could be
 * selected again.
 */
export function hiddenColumns(state: FilterState, banks: string[]): string[] {
  const pickedBanks = state.values[BANK_KEY] ?? [];
  const pickedSides = state.values[SIDE_KEY] ?? [];
  const hidden: string[] = [];
  for (const bank of banks) {
    for (const side of SIDES) {
      const bankOn = pickedBanks.length === 0 || pickedBanks.includes(bank);
      const sideOn = pickedSides.length === 0 || pickedSides.includes(side);
      if (!bankOn || !sideOn) hidden.push(`${bank}__${side}`);
    }
  }
  return hidden;
}
