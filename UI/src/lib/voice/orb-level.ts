/**
 * The recorder's level meter, as the single number the orb's shader wants.
 *
 * `useVoiceSession` already runs an analyser over the one microphone stream and
 * publishes seventeen bars for the composer's meter. The orb ships with its own
 * `getUserMedia` and its own analyser, which would be a second live capture
 * with the opposite echo-cancellation settings and a second recording indicator
 * in the browser chrome. Reusing the bars costs this function and nothing else.
 */

/** What each bar sits at in silence, from the meter that produces them. */
const QUIET_FLOOR = 0.08;

/**
 * The mean above the floor that counts as full deflection.
 *
 * Below 1.0 on purpose. The bars are a spectrum: even a loud voice leaves the
 * high bins near the floor, so a mean of 1.0 is unreachable in practice and
 * scaling to it would leave the orb permanently half-asleep.
 */
const SPEAKING_MEAN = 0.35;

export function orbLevelFromBars(levels: readonly number[]): number {
  if (levels.length === 0) return 0;

  let sum = 0;
  let counted = 0;
  for (const bar of levels) {
    // A non-finite bar poisons a mean silently; dropped rather than propagated.
    if (!Number.isFinite(bar)) continue;
    sum += Math.max(0, bar - QUIET_FLOOR);
    counted += 1;
  }
  if (counted === 0) return 0;

  const mean = sum / counted;
  return Math.min(1, Math.max(0, mean / SPEAKING_MEAN));
}
