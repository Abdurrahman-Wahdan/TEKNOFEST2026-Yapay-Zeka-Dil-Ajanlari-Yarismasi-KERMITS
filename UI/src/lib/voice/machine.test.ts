import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  INITIAL_VOICE_STATE,
  MIN_HOLD_MS,
  isVoiceBusy,
  isVoiceTurnActive,
  stepVoice,
  type VoiceEvent,
  type VoiceState,
} from "./machine.ts";

/** Drive the machine through a sequence and hand back where it ended up. */
function run(events: VoiceEvent[], from: VoiceState = INITIAL_VOICE_STATE) {
  let state = from;
  let effects: string[] = [];
  for (const event of events) {
    const step = stepVoice(state, event);
    state = step.state;
    effects = step.effects;
  }
  return { state, effects };
}

const OPEN: VoiceEvent = { type: "open", at: 0 };
const HELD: VoiceEvent = { type: "release", at: MIN_HOLD_MS + 100 };

const LISTENING = [OPEN, { type: "listening" } as VoiceEvent];
const THINKING = [
  ...LISTENING,
  HELD,
  { type: "recorderStopped" } as VoiceEvent,
  { type: "transcript", text: "en iyi mevduat hangisi" } as VoiceEvent,
];
const SPEAKING: VoiceEvent[] = [
  ...THINKING,
  { type: "answered", kind: "text" },
  { type: "shaped", ok: true },
];

describe("starting a voice turn", () => {
  it("arms the session and takes a fresh run id", () => {
    const { state, effects } = run([OPEN]);
    assert.equal(state.phase, "arming");
    assert.equal(state.runId, 1);
    assert.deepEqual(effects, ["startSession"]);
  });

  it("moves to listening once the microphone is open", () => {
    assert.equal(run(LISTENING).state.phase, "listening");
  });

  it("cancels rather than ends when the key comes up before the microphone does", () => {
    // `end()` does not invalidate a pending getUserMedia, so the stream would
    // resolve into a recorder nobody is watching.
    const { state, effects } = run([OPEN, { type: "release", at: 50 }]);
    assert.equal(state.phase, "closed");
    assert.ok(effects.includes("cancelSession"));
    assert.ok(!effects.includes("endSession"));
  });
});

describe("ending a recording", () => {
  it("sends the recording when the key was held long enough", () => {
    const { state, effects } = run([...LISTENING, HELD]);
    assert.equal(state.phase, "stopping");
    assert.deepEqual(effects, ["endSession"]);
  });

  it("refuses a tap too short to be a question", () => {
    const { state, effects } = run([...LISTENING, { type: "release", at: 100 }]);
    assert.equal(state.phase, "failing");
    assert.equal(state.failure, "tooShort");
    assert.ok(effects.includes("cancelSession"));
  });

  it("sends rather than discards a recording that hit the duration cap", () => {
    const { state, effects } = run([...LISTENING, { type: "holdCap" }]);
    assert.equal(state.phase, "stopping");
    assert.deepEqual(effects, ["endSession"]);
  });
});

describe("turning speech into an answer", () => {
  it("asks the assistant once a transcript arrives", () => {
    const { state, effects } = run(THINKING);
    assert.equal(state.phase, "thinking");
    assert.deepEqual(effects, ["sendQuestion", "startMusic", "armFiller"]);
  });

  it("never asks the assistant about silence", () => {
    const { state } = run([
      ...LISTENING,
      HELD,
      { type: "recorderStopped" },
      { type: "transcript", text: "   " },
    ]);
    assert.equal(state.phase, "failing");
    assert.equal(state.failure, "empty");
  });

  it("shapes the answer for speech and stops the filler", () => {
    const { state, effects } = run([...THINKING, { type: "answered", kind: "text" }]);
    assert.equal(state.phase, "shaping");
    assert.deepEqual(effects, ["clearFiller", "shapeAnswer"]);
  });

  it("never reads a failed turn out as though it were the answer", () => {
    for (const kind of ["error", "empty"] as const) {
      const { state, effects } = run([...THINKING, { type: "answered", kind }]);
      assert.equal(state.phase, "failing");
      assert.equal(state.failure, "answerFailed");
      assert.ok(!effects.includes("shapeAnswer"));
    }
  });

  it("falls back to the deterministic wording when shaping failed", () => {
    const base = [...THINKING, { type: "answered", kind: "text" } as VoiceEvent];
    assert.deepEqual(run([...base, { type: "shaped", ok: true }]).effects, [
      "stopMusic",
      "speakShaped",
    ]);
    assert.deepEqual(run([...base, { type: "shaped", ok: false }]).effects, [
      "stopMusic",
      "speakFallback",
    ]);
  });

  it("stays on screen once the answer has been read, rather than closing", () => {
    const spoken = run([...SPEAKING, { type: "spoken" }]);
    assert.equal(spoken.state.phase, "lingering");
    assert.deepEqual(spoken.effects, []);
  });
});

describe("waiting for the follow-up", () => {
  const LINGERING: VoiceEvent[] = [...SPEAKING, { type: "spoken" }];

  it("starts the next turn on a fresh press, without a run of its own to stop", () => {
    const { state, effects } = run([...LINGERING, { type: "open", at: 900 }]);
    assert.equal(state.phase, "arming");
    // `spoken` only fires once the sound has actually stopped, so unlike the
    // barge-in above there is nothing left playing to cut off.
    assert.deepEqual(effects, ["startSession"]);
  });

  it("closes itself when nobody comes back to it", () => {
    const { state } = run([...LINGERING, { type: "dismiss" }]);
    assert.equal(state.phase, "closed");
  });

  it("closes on the close button and on Escape", () => {
    const { state } = run([...LINGERING, { type: "cancel" }]);
    assert.equal(state.phase, "closed");
  });

  it("takes a fresh run id on the way out, so a late timer cannot reopen it", () => {
    const lingering = run(LINGERING);
    const closed = stepVoice(lingering.state, { type: "dismiss" });
    assert.ok(closed.state.runId > lingering.state.runId);
  });

  it("accepts a press while it waits", () => {
    assert.equal(isVoiceBusy("lingering"), false);
  });

  it("ignores the events of a turn that is already over", () => {
    for (const event of [
      { type: "release", at: 900 },
      { type: "spoken" },
      { type: "recorderStopped" },
    ] as VoiceEvent[]) {
      const lingering = run(LINGERING);
      const after = stepVoice(lingering.state, event);
      assert.equal(after.state, lingering.state);
      assert.deepEqual(after.effects, []);
    }
  });
});

describe("filling the wait with something to hear", () => {
  it("starts the music with the question, and before the first spoken line", () => {
    const { effects } = run(THINKING);
    assert.deepEqual(effects, ["sendQuestion", "startMusic", "armFiller"]);
    // Order matters: the opening line's own delay is zero, so it has to land on
    // something already playing rather than start the sound itself.
    assert.ok(effects.indexOf("startMusic") < effects.indexOf("armFiller"));
  });

  it("keeps playing while the answer is being shaped for speech", () => {
    // Shaping is another one to three seconds of nothing. Going quiet for it
    // would read as the line dropping just before the answer.
    const { effects } = run([...THINKING, { type: "answered", kind: "text" }]);
    assert.ok(!effects.includes("stopMusic"));
  });

  it("stops the music as the answer starts being read, not after", () => {
    const { effects } = run(SPEAKING);
    assert.deepEqual(effects, ["stopMusic", "speakShaped"]);
    assert.ok(effects.indexOf("stopMusic") < effects.indexOf("speakShaped"));
  });

  it("stops the music on every way a turn can end early", () => {
    const ends: VoiceEvent[] = [
      { type: "cancel" },
      { type: "fail", failure: "answerFailed" },
    ];
    for (const event of ends) {
      const { effects } = run([...THINKING, event]);
      assert.ok(effects.includes("stopMusic"), `${event.type} left the music playing`);
    }
  });

  it("stops the music when the assistant answers with an error", () => {
    const { state, effects } = run([...THINKING, { type: "answered", kind: "error" }]);
    assert.equal(state.phase, "failing");
    assert.ok(effects.includes("stopMusic"));
  });

  it("never leaves the music playing behind a dock that has closed", () => {
    // Every phase that can be cancelled, not just the one the wait runs in.
    const phases: VoiceEvent[][] = [THINKING, [...THINKING, { type: "answered", kind: "text" }]];
    for (const events of phases) {
      const { state, effects } = run([...events, { type: "cancel" }]);
      assert.equal(state.phase, "closed");
      assert.ok(effects.includes("stopMusic"));
    }
  });
});

describe("interrupting", () => {
  it("closes from every phase of a running turn", () => {
    const phases: VoiceEvent[][] = [
      [OPEN],
      LISTENING,
      [...LISTENING, HELD],
      [...LISTENING, HELD, { type: "recorderStopped" }],
      THINKING,
      [...THINKING, { type: "answered", kind: "text" }],
    ];
    for (const events of phases) {
      const { state, effects } = run([...events, { type: "cancel" }]);
      assert.equal(state.phase, "closed");
      assert.ok(effects.includes("cancelSession"));
      assert.ok(effects.includes("stopSpeech"));
      assert.ok(effects.includes("clearFiller"));
    }
  });

  it("invalidates the run so a late transcript cannot revive the turn", () => {
    const cancelled = run([...LISTENING, HELD, { type: "cancel" }]);
    const after = stepVoice(cancelled.state, { type: "transcript", text: "merhaba" });
    assert.equal(after.state.phase, "closed");
    assert.deepEqual(after.effects, []);
    assert.ok(cancelled.state.runId > run(LISTENING).state.runId);
  });

  it("does nothing when Escape arrives with no turn running", () => {
    const { state, effects } = run([{ type: "cancel" }]);
    assert.equal(state.runId, 0);
    assert.deepEqual(effects, []);
  });

  it("lets a new press interrupt the answer being read aloud", () => {
    const speaking = run(SPEAKING);
    const { state, effects } = stepVoice(speaking.state, { type: "open", at: 900 });
    assert.equal(state.phase, "arming");
    assert.deepEqual(effects, ["stopSpeech", "startSession"]);
  });

  it("replaces a failure message when the user presses again", () => {
    const failed = run([...LISTENING, { type: "release", at: 10 }]);
    assert.equal(stepVoice(failed.state, OPEN).state.phase, "arming");
  });

  it("does not restart a failure the user is already being shown", () => {
    const failed = run([...LISTENING, { type: "release", at: 10 }]);
    const again = stepVoice(failed.state, { type: "fail", failure: "unavailable" });
    assert.equal(again.state, failed.state);
    assert.deepEqual(again.effects, []);
  });

  it("clears a failure when it is dismissed", () => {
    const failed = run([...LISTENING, { type: "release", at: 10 }]);
    const { state } = stepVoice(failed.state, { type: "dismiss" });
    assert.equal(state.phase, "closed");
    assert.equal(state.failure, null);
  });
});

describe("describing the current phase", () => {
  it("counts every phase of a running turn as active", () => {
    assert.equal(isVoiceTurnActive("listening"), true);
    assert.equal(isVoiceTurnActive("speaking"), true);
    assert.equal(isVoiceTurnActive("closed"), false);
    assert.equal(isVoiceTurnActive("failing"), false);
  });

  it("does not count a dock waiting for the next question as a running turn", () => {
    // Nothing is in flight behind it, so there is nothing to interrupt.
    assert.equal(isVoiceTurnActive("lingering"), false);
  });

  it("does not count reading the answer aloud as too busy to interrupt", () => {
    assert.equal(isVoiceBusy("thinking"), true);
    assert.equal(isVoiceBusy("speaking"), false);
    assert.equal(isVoiceBusy("closed"), false);
  });
});
