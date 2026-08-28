"use client";

import { audioContext } from "@/lib/chat/speech";

import {
  holdMusicLoop,
  type HoldEvent,
} from "./hold-music-score.ts";

/**
 * The hold music, played on the same graph the answer is read through.
 *
 * The score is next door and pure; this is the half that cannot be tested,
 * which is oscillators, envelopes and the look-ahead that keeps the loop from
 * running dry. The decisions are all in `hold-music-score.ts`.
 *
 * **Never a reason for a turn to fail.** Every entry point swallows its own
 * errors. A browser without `AudioContext`, an autoplay policy that refuses the
 * resume, a device that has run out of voices -- none of that is worth turning
 * a perfectly good answer into an error message, and the wait is merely quieter
 * without it. Hence the try/catch on all three exports and nothing thrown out
 * of this file.
 *
 * Module state rather than a class, matching `speech.ts` next to it: there is
 * one pair of speakers, so "is the hold music playing" is one global fact, and
 * a second instance would be a second loop on top of the first.
 */

/**
 * How loud it sits, and how far it drops while something is being said.
 *
 * These are the two numbers to reach for, so here is what they are measured
 * against. `speech.ts` plays the voice at unity -- it divides its PCM by 32768,
 * so a reading peaks near 1.0. The per-voice weights below take another half
 * off, which puts the music at roughly -10dB under the voice at `HOLD_GAIN` and
 * about -29dB at `DUCKED_GAIN`. The normal level is intentionally close to the
 * voice while the user is waiting; the ducked level keeps the answer clearly
 * in front once speech begins.
 */
const HOLD_GAIN = 0.6;
const DUCKED_GAIN = 0.07;

/** How quickly the level moves. Slow enough to be a fade, not a click. */
const DUCK_SECONDS = 0.18;
const FADE_OUT_SECONDS = 0.35;

/**
 * How far ahead loops are booked, and how often that is topped up.
 *
 * The loop is ~27s long, so one look-ahead is nearly always enough; the
 * interval exists for the case where it is not, and it is deliberately much
 * shorter than the window it fills so a throttled tick cannot leave a gap.
 */
const LOOKAHEAD_SECONDS = 4;
const PUMP_MS = 1_000;

/** Relative weights, before `HOLD_GAIN`. The bass is the quieter of the two. */
const VOICE_GAIN: Record<HoldEvent["voice"], number> = {
  bass: 0.34,
  arpeggio: 0.5,
};

/** Sine for the bass so it stays a pitch rather than a texture. */
const VOICE_WAVE: Record<HoldEvent["voice"], OscillatorType> = {
  bass: "sine",
  arpeggio: "triangle",
};

let master: GainNode | null = null;
let playing: OscillatorNode[] = [];
let pump: ReturnType<typeof setInterval> | null = null;
/** Where the next loop begins on the context clock. */
let nextLoopAt = 0;

function book(ctx: AudioContext, event: HoldEvent, into: GainNode): void {
  const osc = ctx.createOscillator();
  const envelope = ctx.createGain();

  osc.type = VOICE_WAVE[event.voice];
  osc.frequency.setValueAtTime(event.hz, event.at);

  /*
    A plucked shape: up in 15ms, then decaying for the rest of the note. Booked
    as automation rather than driven from a timer, so the envelope is on the
    audio clock and stays right even while the main thread is busy shipping a
    transcript or re-rendering the dock.
  */
  const peak = VOICE_GAIN[event.voice];
  envelope.gain.setValueAtTime(0.0001, event.at);
  envelope.gain.linearRampToValueAtTime(peak, event.at + 0.015);
  // Exponential, and never to zero: `exponentialRampToValueAtTime` rejects 0,
  // and a linear tail on a decaying note sounds like a fader being pulled.
  envelope.gain.exponentialRampToValueAtTime(0.0001, event.at + event.seconds);

  osc.connect(envelope);
  envelope.connect(into);
  osc.start(event.at);
  osc.stop(event.at + event.seconds);

  playing.push(osc);
  osc.onended = () => {
    playing = playing.filter((node) => node !== osc);
    try {
      envelope.disconnect();
    } catch {
      // Already torn down by `stopHoldMusic`.
    }
  };
}

/** Book every loop that starts inside the look-ahead window. */
function fill(ctx: AudioContext, into: GainNode): void {
  // A tick that arrived very late -- a backgrounded tab, a long task -- would
  // otherwise book several loops at once, all of them in the past, and every
  // note in them would fire at the same instant. Skipping to the present
  // costs a seam and is the only outcome that is not a pile-up.
  if (nextLoopAt < ctx.currentTime) nextLoopAt = ctx.currentTime + 0.05;

  while (nextLoopAt < ctx.currentTime + LOOKAHEAD_SECONDS) {
    const loop = holdMusicLoop(nextLoopAt);
    for (const event of loop.events) book(ctx, event, into);
    nextLoopAt += loop.seconds;
  }
}

/**
 * Start playing, or carry on if already playing.
 *
 * Idempotent: the machine fires `startMusic` once per turn, but a second call
 * must not stack a second loop on the first.
 */
export function startHoldMusic(): void {
  if (master) return;
  try {
    const ctx = audioContext();
    // Not awaited. The context was already unlocked inside the keydown by
    // `primeSpeech`, so this is a no-op in the ordinary case; awaiting it would
    // only delay the first bar in the case where it is not.
    if (ctx.state === "suspended") void ctx.resume().catch(() => undefined);

    const gain = ctx.createGain();
    gain.gain.setValueAtTime(HOLD_GAIN, ctx.currentTime);
    gain.connect(ctx.destination);
    master = gain;

    nextLoopAt = ctx.currentTime + 0.06;
    fill(ctx, gain);
    pump = setInterval(() => {
      if (!master) return;
      try {
        fill(audioContext(), master);
      } catch {
        // The context went away underneath us. Nothing to recover.
      }
    }, PUMP_MS);
  } catch {
    stopHoldMusic();
  }
}

/**
 * Duck under a spoken line, or come back up after it.
 *
 * `setTargetAtTime` rather than a ramp, so overlapping calls -- a filler
 * finishing as the answer begins -- glide from wherever the level actually is
 * instead of jumping to where the last ramp assumed it would be.
 */
export function duckHoldMusic(ducked: boolean): void {
  if (!master) return;
  try {
    const ctx = audioContext();
    master.gain.setTargetAtTime(
      ducked ? DUCKED_GAIN : HOLD_GAIN,
      ctx.currentTime,
      DUCK_SECONDS,
    );
  } catch {
    // Level unchanged. Not worth failing a turn over.
  }
}

/**
 * Stop, fading rather than cutting.
 *
 * The music ends because the answer is about to be read, and a hard stop on the
 * beat before someone starts talking is more noticeable than the music was.
 * The oscillators are stopped after the fade; `master` is dropped immediately
 * so a `startHoldMusic` racing this one builds a fresh chain rather than
 * feeding the one that is on its way out.
 */
export function stopHoldMusic(): void {
  if (pump) clearInterval(pump);
  pump = null;

  const gain = master;
  const nodes = playing;
  master = null;
  playing = [];
  nextLoopAt = 0;
  if (!gain) return;

  try {
    const ctx = audioContext();
    const endsAt = ctx.currentTime + FADE_OUT_SECONDS;
    gain.gain.cancelScheduledValues(ctx.currentTime);
    gain.gain.setValueAtTime(Math.max(gain.gain.value, 0.0001), ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, endsAt);
    for (const osc of nodes) {
      try {
        osc.stop(endsAt);
      } catch {
        // Already ended; stopping a finished node throws.
      }
    }
    setTimeout(
      () => {
        try {
          gain.disconnect();
        } catch {
          // Already disconnected.
        }
      },
      FADE_OUT_SECONDS * 1000 + 50,
    );
  } catch {
    try {
      gain.disconnect();
    } catch {
      // Nothing left to do.
    }
  }
}
