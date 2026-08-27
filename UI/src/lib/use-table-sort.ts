"use client";

import { useCallback, useState } from "react";

import type { SortState } from "@/lib/table-filter";

/**
 * A table column sort, in the app's three-click form: ascending, descending,
 * then off.
 *
 * The third click is the point of it. Clearing a sort is how a reader gets back
 * to the producer's own ordering, which is itself information — a ranking the
 * table shipped with, not an accident of arrival order.
 *
 * The state lives with the caller rather than inside `ProducedTable` because
 * only the caller knows when it has to go: `Comparator` clears it on every new
 * comparison and on a category switch, `CompareTablesBrowser` clears it when a
 * different table is opened. A sort carried across either would be ordering one
 * table's rows by a column the reader can no longer see.
 *
 * There is deliberately no raw `setSort` in the return. Nothing needs to set an
 * arbitrary sort — the three call sites only toggle and clear — and exposing
 * the setter is how this ends up hand-copied into a fourth lookalike.
 */
export function useTableSort(): {
  sort: SortState | null;
  toggleSort: (key: string) => void;
  resetSort: () => void;
} {
  const [sort, setSort] = useState<SortState | null>(null);

  // `useCallback` is load-bearing, not tidiness: callers feed `sort` and this
  // toggle through `useMemo` dependency lists around `sortRows`, and a fresh
  // function identity each render would invalidate them every time.
  const toggleSort = useCallback((key: string) => {
    setSort((current) =>
      current?.key === key
        ? current.direction === "asc"
          ? { key, direction: "desc" }
          : null
        : { key, direction: "asc" },
    );
  }, []);

  const resetSort = useCallback(() => setSort(null), []);

  return { sort, toggleSort, resetSort };
}
