/**
 * What plays under the wait, as note data with no browser in it.
 *
 * The wait is thirty to sixty seconds of nothing, and the spoken holding line
 * only covers a second or two of each ten. What a phone system does with the
 * other eight is play music, and the reason it works is that continuous sound
 * is proof the line is still open in a way that a periodic sentence is not.
 *
 * **It is written here, not fetched.** A recording would have been better to
 * listen to, but Mozart's *compositions* are public domain and Mozart
 * *recordings* are not, and there is no package that ships a licensed one. So
 * this is a progression rather than a piece: `I – vi – IV – V – I – iii – IV –
 * V`, arpeggiated, which is the idiom without being anyone's performance. It is
 * also what real hold music is, and for the same reason.
 *
 * Pure and separate from the player next door for the usual reason in this
 * folder: `npm test` runs `node --experimental-strip-types` with no DOM, so the
 * arithmetic that decides *when* each note sounds is testable and the
 * `OscillatorNode` that sounds it is not.
 */

/** A note the player has to book: a pitch, a moment, and how long it rings. */
export type HoldEvent = {
  hz: number;
  /** Seconds on the `AudioContext` clock, not a delay. */
  at: number;
  seconds: number;
  /** Which of the two lines it belongs to, since they are voiced differently. */
  voice: "bass" | "arpeggio";
};

/**
 * Slow. This is furniture, not something to listen to.
 *
 * At 72 the eight-bar progression lasts a little over twenty-six seconds, which
 * is long enough that a caller waiting a minute does not hear the seam twice.
 */
export const HOLD_BPM = 72;

const BEATS_PER_BAR = 4;

/**
 * The figure each bar plays, as indices into that bar's chord.
 *
 * Eight eighth-notes, up and back down. Alberti's shape rather than Alberti's
 * notes: a rocking pattern reads as motion without ever arriving anywhere,
 * which is what lets it sit under a sentence instead of competing with one.
 */
const ARPEGGIO: readonly number[] = [0, 1, 2, 3, 2, 1, 0, 1];

/**
 * The progression, as MIDI note numbers. 60 is middle C.
 *
 * The two lines are pitched apart on purpose, but not as far apart as would be
 * comfortable to claim. Speech formants cover roughly 300Hz to 3.4kHz, and
 * *nothing musical* sits outside that: a chord voiced below it is rumble and a
 * chord voiced above it is a whistle. So the arpeggio is inside the voice's
 * band and always will be, and what keeps the line intelligible is the level
 * and the duck, not separation.
 *
 * What the voicing does buy is the bass. It stays under 140Hz, below where a
 * voice's own fundamental sits, so the one part of the music with sustained
 * energy is not sharing a register with the one part of the speech that carries
 * the pitch. The arpeggio then stays inside an octave and a half from E4 up,
 * which is narrow enough that no bar jumps out of the texture -- an earlier
 * voicing ran to G6 and read as a doorbell over the top of the answer.
 */
const PROGRESSION: readonly { bass: number; notes: readonly number[] }[] = [
  { bass: 48, notes: [72, 76, 79, 84] }, // C  major -- C5 E5 G5 C6
  { bass: 45, notes: [69, 72, 76, 81] }, // A  minor -- A4 C5 E5 A5
  { bass: 41, notes: [65, 69, 72, 77] }, // F  major -- F4 A4 C5 F5
  { bass: 43, notes: [67, 71, 74, 79] }, // G  major -- G4 B4 D5 G5
  { bass: 48, notes: [72, 76, 79, 84] }, // C  major
  { bass: 40, notes: [64, 67, 71, 76] }, // E  minor -- E4 G4 B4 E5
  { bass: 41, notes: [65, 69, 72, 77] }, // F  major
  { bass: 43, notes: [67, 71, 74, 79] }, // G  major
];

/**
 * How long a note rings relative to its slot.
 *
 * Over 1 for the arpeggio so each note is still sounding when the next starts:
 * that overlap is the difference between a phrase and a row of beeps. It also
 * means the last note of a pass rings a fraction of a beat into the next one,
 * which is correct rather than a bug -- the player books passes back to back,
 * and a loop whose final note stopped dead at the seam would be the one place
 * in the piece where the phrasing broke. Under 1 for the bass so the bar has a
 * seam to breathe at.
 */
const ARPEGGIO_RING = 1.6;
const BASS_RING = 0.9;

/** Equal temperament, A4 = 440Hz at MIDI 69. */
export function noteHz(midi: number): number {
  return 440 * 2 ** ((midi - 69) / 12);
}

/** How long one pass through the progression lasts, in seconds. */
export function holdLoopSeconds(bpm: number = HOLD_BPM): number {
  return (PROGRESSION.length * BEATS_PER_BAR * 60) / bpm;
}

/**
 * One pass through the progression, booked from `startAt`.
 *
 * Absolute times rather than offsets, because the caller is scheduling against
 * an `AudioContext` clock that does not stop when a timer is late. Handing back
 * offsets would mean the player adding `currentTime` at the moment it happened
 * to run, and a loop booked a hundred milliseconds late leaves a hundred
 * milliseconds of silence at the seam.
 */
export function holdMusicLoop(
  startAt: number,
  bpm: number = HOLD_BPM,
): { events: HoldEvent[]; seconds: number } {
  const beat = 60 / bpm;
  const eighth = beat / 2;
  const events: HoldEvent[] = [];

  PROGRESSION.forEach((chord, bar) => {
    const barAt = startAt + bar * BEATS_PER_BAR * beat;

    events.push({
      hz: noteHz(chord.bass),
      at: barAt,
      seconds: BEATS_PER_BAR * beat * BASS_RING,
      voice: "bass",
    });

    ARPEGGIO.forEach((step, index) => {
      events.push({
        hz: noteHz(chord.notes[step]),
        at: barAt + index * eighth,
        seconds: eighth * ARPEGGIO_RING,
        voice: "arpeggio",
      });
    });
  });

  return { events, seconds: holdLoopSeconds(bpm) };
}
