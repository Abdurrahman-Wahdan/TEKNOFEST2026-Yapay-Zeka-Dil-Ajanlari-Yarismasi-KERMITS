/**
 * What colour the composer's context ring is, and why.
 *
 * Measured against the *compaction threshold*, not the window. A thread at 60%
 * of its window is at 86% of a 0.7 threshold, and the second number is the one
 * worth reacting to: compaction is what happens next, not running out of room.
 * Thresholds set here are therefore fractions of `compact_at_tokens`, so moving
 * COMPACT_AT_FRACTION in settings moves the colours with it rather than leaving
 * them describing a threshold that no longer exists.
 */

/** Past this share of the way to compaction, it is worth noticing. */
export const NEAR = 0.75;

/** Past this, compaction happens within a turn or two. */
export const IMMINENT = 0.9;

export type RingTone = "filling" | "near" | "imminent";

export function ringTone(usedTokens: number, compactAtTokens: number): RingTone {
  // No threshold means nothing to be near. Guarded rather than divided by:
  // a zero here would make every ring red on a misconfigured deployment.
  if (compactAtTokens <= 0) return "filling";
  const ratio = usedTokens / compactAtTokens;
  if (ratio >= IMMINENT) return "imminent";
  if (ratio >= NEAR) return "near";
  return "filling";
}

/**
 * The token for a tone.
 *
 * Brand blue while it fills, because that is the app's colour for a thing
 * working normally; the warning and danger tokens carry the same meaning here as
 * everywhere else in the palette.
 */
export function ringColor(usedTokens: number, compactAtTokens: number): string {
  switch (ringTone(usedTokens, compactAtTokens)) {
    case "imminent":
      return "var(--danger)";
    case "near":
      return "var(--warn)";
    default:
      return "var(--primary)";
  }
}
