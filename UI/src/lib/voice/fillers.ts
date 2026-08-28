/**
 * When the assistant says it is still working, while the user waits.
 *
 * The wait is the reason this exists. A ten-bank comparison fans out for about
 * thirty seconds and the output guard then holds the finished answer for
 * another eight to thirty-one before a single token is released -- and in voice
 * mode there is no screen to watch, so a minute of silence is indistinguishable
 * from a crash.
 *
 * **Two lines, not a rotation.** This used to draw from a shuffled bag of seven
 * phrases on a jittered twenty-second beat, so the waiting never sounded like a
 * recording. The cost of that variety was silence: the first phrase did not
 * arrive until ten seconds in, which is a long time to wonder whether the
 * question was even heard, and the gaps afterwards were long enough that the
 * user was back to watching the clock between them. Reassurance beats variety
 * here. There is now an opening line that goes out the instant the transcript
 * lands -- it acknowledges the question rather than reporting on progress --
 * and one holding line after it, on a steady ten-second beat until the answer
 * comes. Hearing the same sentence repeat is the point: it is what a person on
 * the other end of a phone call does.
 *
 * The two phrases being fixed also happens to be what the backend's audio cache
 * is shaped for (`api/voice_speech.py`, keyed on the exact text), which seven
 * rotating phrases could never stay resident in.
 */

/**
 * How long the opening line waits. It does not.
 *
 * Zero, not "small": this fires the moment the machine enters `thinking`, which
 * is the moment the user stopped talking, and anything else is a pause where an
 * acknowledgement should be.
 */
export const FILLER_OPENING_MS = 0;

/**
 * The gap between holding lines.
 *
 * Measured from the end of the previous one finishing playing, never on a wall
 * clock -- see `useVoiceMode.armFiller`. A slow reading would otherwise have
 * the next one stack on top of it.
 */
export const FILLER_REPEAT_MS = 10_000;

/**
 * How long to wait before the nth thing said during one wait.
 *
 * `attempt` counts what has already been said this turn, so 0 is the opening
 * line. No `random` any more: the cadence is deliberately regular, because a
 * steady beat is what reads as "still here" rather than as an afterthought.
 */
export function fillerDelayMs(attempt: number): number {
  return attempt <= 0 ? FILLER_OPENING_MS : FILLER_REPEAT_MS;
}

/**
 * Whether the nth thing said is the opening line rather than the holding one.
 *
 * The phrases themselves stay in the caller, as two literal `t()` calls, so
 * `npm run i18n:check` can see them. Resolving a key here from a variable would
 * put them back out of the scanner's reach -- which is exactly how the seven
 * phrases this replaces went unchecked behind `t.raw("fillers")`.
 */
export function isOpeningFiller(attempt: number): boolean {
  return attempt <= 0;
}
