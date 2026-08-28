import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  HOLD_BPM,
  holdLoopSeconds,
  holdMusicLoop,
  noteHz,
} from "./hold-music-score.ts";

/**
 * Where a voice's own fundamental sits.
 *
 * Not the formant band -- that runs to 3.4kHz and no musical register avoids
 * it, which is why the score does not claim to. This is the narrower thing the
 * voicing actually guarantees.
 */
const VOICE_FUNDAMENTAL_HZ = 300;

describe("pitching the hold music", () => {
  it("tunes to A440", () => {
    assert.equal(noteHz(69), 440);
  });

  it("doubles every octave", () => {
    assert.ok(Math.abs(noteHz(81) - 880) < 1e-9);
    assert.ok(Math.abs(noteHz(57) - 220) < 1e-9);
  });

  it("keeps the sustained bass out of the voice's own register", () => {
    // The bass is the only line that holds a pitch for a whole bar, so it is
    // the only one that would sit on top of the speech's fundamental for long
    // enough to muddy it.
    for (const event of holdMusicLoop(0).events) {
      if (event.voice !== "bass") continue;
      assert.ok(event.hz < VOICE_FUNDAMENTAL_HZ, `bass at ${event.hz}Hz is in the voice`);
    }
  });

  it("keeps the arpeggio inside one calm register", () => {
    // An earlier voicing ran from A4 to G6 and the high bars read as a
    // doorbell. The span, not the absolute pitch, is what makes it furniture.
    const hz = holdMusicLoop(0)
      .events.filter((e) => e.voice === "arpeggio")
      .map((e) => e.hz);
    const span = Math.max(...hz) / Math.min(...hz);
    assert.ok(span <= 4, `arpeggio spans ${span.toFixed(2)} octaves' worth of ratio`);
    assert.ok(Math.min(...hz) > VOICE_FUNDAMENTAL_HZ);
  });
});

describe("laying the loop out in time", () => {
  it("runs long enough that a minute-long wait does not hear the seam twice", () => {
    const seconds = holdLoopSeconds();
    assert.ok(seconds > 20, `loop is only ${seconds}s`);
    assert.equal(seconds, (8 * 4 * 60) / HOLD_BPM);
  });

  it("books every note from the moment it is given, never from zero", () => {
    // Absolute times, because the caller is scheduling against a clock that
    // does not stop while a timer is late.
    const at = 1234.5;
    const { events } = holdMusicLoop(at);
    assert.ok(events.length > 0);
    for (const event of events) assert.ok(event.at >= at, `${event.at} < ${at}`);
    assert.equal(Math.min(...events.map((e) => e.at)), at);
  });

  it("is the same loop wherever it is booked", () => {
    const base = holdMusicLoop(0).events;
    const later = holdMusicLoop(500).events;
    assert.equal(base.length, later.length);
    for (let i = 0; i < base.length; i += 1) {
      assert.equal(later[i].hz, base[i].hz);
      assert.equal(later[i].voice, base[i].voice);
      assert.ok(Math.abs(later[i].at - base[i].at - 500) < 1e-9);
    }
  });

  it("starts the next pass exactly where this one ends, so the seam is silent", () => {
    const first = holdMusicLoop(0);
    const second = holdMusicLoop(first.seconds);
    const lastBarStart = Math.max(
      ...first.events.filter((e) => e.voice === "bass").map((e) => e.at),
    );
    assert.ok(second.events[0].at > lastBarStart);
    assert.equal(second.events[0].at, first.seconds);
  });

  it("gives every bar a bass note and eight arpeggio notes", () => {
    const { events } = holdMusicLoop(0);
    assert.equal(events.filter((e) => e.voice === "bass").length, 8);
    assert.equal(events.filter((e) => e.voice === "arpeggio").length, 64);
  });

  it("overlaps the arpeggio so it phrases instead of ticking", () => {
    const arpeggio = holdMusicLoop(0)
      .events.filter((e) => e.voice === "arpeggio")
      .slice(0, 8);
    for (let i = 1; i < arpeggio.length; i += 1) {
      const previousEnds = arpeggio[i - 1].at + arpeggio[i - 1].seconds;
      assert.ok(previousEnds > arpeggio[i].at, `gap before note ${i}`);
    }
  });

  it("lets each bar breathe before the next bass note", () => {
    const bass = holdMusicLoop(0).events.filter((e) => e.voice === "bass");
    for (let i = 1; i < bass.length; i += 1) {
      assert.ok(bass[i - 1].at + bass[i - 1].seconds < bass[i].at);
    }
  });

  it("rings across the seam by no more than it rings across any other note", () => {
    /*
      The last note of a pass *should* still be sounding when the next pass
      starts -- the player books passes back to back, and stopping dead at the
      seam would be the one place the phrasing broke. What must not happen is
      the seam being a bigger overlap than the ones inside the loop, which would
      mean a chord change landing on top of itself.
    */
    const { events, seconds } = holdMusicLoop(0);
    const overhang = Math.max(...events.map((e) => e.at + e.seconds - seconds));
    const arpeggio = events.filter((e) => e.voice === "arpeggio");
    const inner = arpeggio[1].at + arpeggio[1].seconds - arpeggio[2].at;

    assert.ok(overhang > 0, "the seam is a hard stop");
    assert.ok(overhang <= inner + 1e-9, `seam overhang ${overhang}s exceeds ${inner}s`);
  });

  it("never leaves a bass note ringing into the next pass", () => {
    // The bass carries the harmony, so one holding over a chord change is the
    // one overlap that would actually sound wrong.
    const { events, seconds } = holdMusicLoop(0);
    for (const event of events) {
      if (event.voice !== "bass") continue;
      assert.ok(event.at + event.seconds <= seconds + 1e-9, `bass at ${event.hz}Hz runs past`);
    }
  });
});
