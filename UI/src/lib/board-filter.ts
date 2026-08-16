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
 * A key that was never touched means everything, in both dimensions -- that
 * is what stops the two from cancelling each other out, the same way it did
 * before this was split into two keys: they used to be inferred from the
 * hidden columns, so clearing one made the other look cleared too and
 * nothing could be selected again.
 *
 * Untouched is `undefined`, not `[]`. Collapsing "nobody's picked anything
 * yet" and "the user picked nothing on purpose" onto the same empty array
 * made the second one unreachable: toggling every bank off produced `[]`,
 * which read back as "no filter" and every bank re-appeared, so the toggle
 * looked stuck between all-on and all-on. `state.values[key]` is left as
 * whatever was actually stored -- `undefined` until touched, a real array
 * (possibly empty) after.
 */
export function hiddenColumns(state: FilterState, banks: string[]): string[] {
  const pickedBanks = state.values[BANK_KEY];
  const pickedSides = state.values[SIDE_KEY];
  const hidden: string[] = [];
  for (const bank of banks) {
    for (const side of SIDES) {
      const bankOn = pickedBanks === undefined || pickedBanks.includes(bank);
      const sideOn = pickedSides === undefined || pickedSides.includes(side);
      if (!bankOn || !sideOn) hidden.push(`${bank}__${side}`);
    }
  }
  return hidden;
}
