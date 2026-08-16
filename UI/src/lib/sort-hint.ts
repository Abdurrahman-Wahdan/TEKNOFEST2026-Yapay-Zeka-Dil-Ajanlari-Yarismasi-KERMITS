import type { ResolvedColumn } from "./contract";

/**
 * The marker beside a heading that is currently sorted.
 *
 * Alphabetical columns say "A–Z" / "Z–A" and numeric ones use arrows, because
 * an arrow on a name column is ambiguous: up could mean A first or Z first,
 * and the only way to find out was to click it and look.
 *
 * Only ever called for an applied sort. An unsorted column shows nothing at
 * all -- a permanent marker on every heading is indistinguishable from a sort
 * that is on, which is worse than no marker.
 */
export function sortHint(
  column: Pick<ResolvedColumn, "type">,
  direction: "asc" | "desc",
): string {
  const alphabetical =
    column.type === "text" || column.type === "badge" || column.type === "bank";
  if (alphabetical) return direction === "desc" ? "Z–A" : "A–Z";
  return direction === "desc" ? "▼" : "▲";
}
