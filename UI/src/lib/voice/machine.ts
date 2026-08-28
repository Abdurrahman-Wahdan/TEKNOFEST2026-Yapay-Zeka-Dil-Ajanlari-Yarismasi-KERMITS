/**
 * The voice turn, as a state machine with no browser in it.
 *
 * A voice turn has three independent async chains in flight at once -- the
 * recorder's `stop` event, the transcription fetch, and the agent turn -- and
 * none of them can be killed synchronously. So the transitions and the effects
 * they fire live here, where they can be tested exhaustively, and the hook next
 * door is left holding nothing but timers and refs.
 *
 * `runId` is the whole cancellation story. Every async continuation in the hook
 * reopens with `if (run !== runIdRef.current) return;`, and every event that
 * ends a turn bumps it. That is what makes a transcript arriving after Escape a
 * no-op rather than a question nobody asked.
 *
 * The music is the exception to "the effects are named, not performed": it is
 * the only one whose lifetime spans two phases. It starts with `thinking` and
 * stops on the way into `speaking`, so the whole wait -- fanning out, the output
 * guard, and the rewrite for speech -- has something playing under it.
 *
 * A turn does not end when the answer stops playing. It lands in `lingering`,
 * which is the dock still on screen with nothing running behind it, waiting to
 * see whether the next thing the user says is the next question. That is what
 * makes a conversation out of what used to be a sequence of separate requests.
 */

export type VoicePhase =
  | "closed"
  | "arming"
  | "listening"
  | "stopping"
  | "transcribing"
  | "thinking"
  | "shaping"
  | "speaking"
  | "lingering"
  | "failing";

export type VoiceFailure =
  | "tooShort"
  | "empty"
  | "permissionDenied"
  | "unavailable"
  | "transcriptionFailed"
  | "answerFailed"
  | "speechFailed"
  | "busy";

/**
 * How long the key must be held before it counts as speech.
 *
 * The recorder emits a chunk every 250ms (`useVoiceSession` calls
 * `recorder.start(250)`), so anything shorter is very likely a single empty
 * chunk, and a tap on the space bar is far more likely to be a mis-press than
 * a question.
 */
export const MIN_HOLD_MS = 400;

/**
 * When a held key stops meaning "still talking".
 *
 * Sends rather than discards: a minute of real speech thrown away because a key
 * stuck is worse than a long recording, and the transcript still has to survive
 * Whisper's own limits either way.
 */
export const MAX_HOLD_MS = 60_000;

/**
 * How long the dock waits after the answer before closing itself.
 *
 * Long enough to ask the follow-up the answer just prompted -- which is the
 * whole reason the dock outlives the reading -- and short enough that a dock
 * nobody came back to is not still sitting over the page a minute later.
 * Anything the user does ends the wait: Space starts the next turn, and the
 * close button and Escape shut it early.
 */
export const LINGER_MS = 12_000;

export type VoiceState = {
  phase: VoicePhase;
  failure: VoiceFailure | null;
  runId: number;
  /** When the key went down, for the minimum-hold check. */
  pressedAt: number;
};

export type VoiceEvent =
  | { type: "open"; at: number }
  | { type: "listening" }
  | { type: "release"; at: number }
  | { type: "holdCap" }
  | { type: "recorderStopped" }
  | { type: "transcript"; text: string }
  | { type: "answered"; kind: "text" | "error" | "empty" }
  | { type: "shaped"; ok: boolean }
  | { type: "spoken" }
  | { type: "fail"; failure: VoiceFailure }
  | { type: "cancel" }
  | { type: "dismiss" };

/**
 * What the hook must do about a transition.
 *
 * Named rather than performed here so the table stays pure. `cancelSession` and
 * `endSession` are two different things and the difference is a live
 * microphone: `useVoiceSession.end()` does not bump its own generation, so
 * calling it while `getUserMedia` is still pending leaves the resolved stream
 * recording with nobody watching. Only `cancel()` invalidates the request.
 */
export type VoiceEffect =
  | "startSession"
  | "cancelSession"
  | "endSession"
  | "sendQuestion"
  | "shapeAnswer"
  | "speakShaped"
  | "speakFallback"
  | "stopSpeech"
  | "armFiller"
  | "clearFiller"
  | "startMusic"
  | "stopMusic";

export const INITIAL_VOICE_STATE: VoiceState = {
  phase: "closed",
  failure: null,
  runId: 0,
  pressedAt: 0,
};

export type VoiceStep = { state: VoiceState; effects: VoiceEffect[] };

const OPEN_PHASES: ReadonlySet<VoicePhase> = new Set<VoicePhase>([
  "arming",
  "listening",
  "stopping",
  "transcribing",
  "thinking",
  "shaping",
  "speaking",
]);

/**
 * Whether a turn is running, as opposed to closed, lingering, or failed.
 *
 * `lingering` is deliberately not one: the dock is on screen but nothing is in
 * flight behind it, so there is nothing for a new press to interrupt.
 */
export function isVoiceTurnActive(phase: VoicePhase): boolean {
  return OPEN_PHASES.has(phase);
}

/**
 * Whether a fresh Space press must be refused while this phase is showing.
 *
 * False for `speaking` and `lingering`, and those two are the point. Cutting
 * the assistant off mid-sentence to ask the next thing is how people actually
 * talk, and the dock lingering afterwards exists precisely so that next thing
 * does not have to start from a closed overlay.
 */
export function isVoiceBusy(phase: VoicePhase): boolean {
  return isVoiceTurnActive(phase) && phase !== "speaking";
}

function open(state: VoiceState, at: number, extra: VoiceEffect[] = []): VoiceStep {
  return {
    state: {
      phase: "arming",
      failure: null,
      runId: state.runId + 1,
      pressedAt: at,
    },
    effects: [...extra, "startSession"],
  };
}

function close(state: VoiceState, effects: VoiceEffect[]): VoiceStep {
  return {
    state: { ...INITIAL_VOICE_STATE, runId: state.runId + 1 },
    effects,
  };
}

function fail(state: VoiceState, failure: VoiceFailure, effects: VoiceEffect[]): VoiceStep {
  return {
    state: { phase: "failing", failure, runId: state.runId + 1, pressedAt: 0 },
    effects,
  };
}

function stay(state: VoiceState): VoiceStep {
  return { state, effects: [] };
}

export function stepVoice(state: VoiceState, event: VoiceEvent): VoiceStep {
  // Cancel and dismiss are answered first, so no phase can forget to handle
  // Escape. A cancel from `closed` is a no-op rather than a runId bump, or
  // every stray Escape on the page would invalidate nothing at increasing cost.
  if (event.type === "cancel") {
    if (state.phase === "closed") return stay(state);
    return close(state, ["clearFiller", "stopMusic", "stopSpeech", "cancelSession"]);
  }
  if (event.type === "dismiss") {
    // The two phases that sit there until something ends them: a failure that
    // has been read, and a dock nobody came back to.
    const waiting = state.phase === "failing" || state.phase === "lingering";
    return waiting ? close(state, []) : stay(state);
  }
  if (event.type === "fail") {
    // A failure the machine is already showing is not a second failure. The
    // session reports `error` as a sticky phase, so without this every render
    // while the message is up would take a fresh run id.
    if (state.phase === "closed" || state.phase === "failing") return stay(state);
    return fail(state, event.failure, [
      "clearFiller",
      "stopMusic",
      "stopSpeech",
      "cancelSession",
    ]);
  }

  switch (state.phase) {
    case "closed":
      return event.type === "open" ? open(state, event.at) : stay(state);

    case "failing":
      // A new press replaces the message rather than queueing behind it.
      return event.type === "open" ? open(state, event.at) : stay(state);

    case "arming":
      if (event.type === "listening") {
        return { state: { ...state, phase: "listening" }, effects: [] };
      }
      // Released before the microphone was even open. `cancelSession`, never
      // `endSession` -- see the note on VoiceEffect.
      if (event.type === "release") {
        return close(state, ["cancelSession"]);
      }
      return stay(state);

    case "listening":
      if (event.type === "release") {
        if (event.at - state.pressedAt < MIN_HOLD_MS) {
          return fail(state, "tooShort", ["cancelSession"]);
        }
        return { state: { ...state, phase: "stopping" }, effects: ["endSession"] };
      }
      if (event.type === "holdCap") {
        return { state: { ...state, phase: "stopping" }, effects: ["endSession"] };
      }
      return stay(state);

    case "stopping":
      if (event.type === "recorderStopped") {
        return { state: { ...state, phase: "transcribing" }, effects: [] };
      }
      return stay(state);

    case "transcribing":
      if (event.type === "transcript") {
        // Silence never reaches the agent: an empty question is answered as
        // though the user had said something, which is worse than saying so.
        if (!event.text.trim()) return fail(state, "empty", []);
        return {
          state: { ...state, phase: "thinking" },
          // The music starts before the first line rather than with it: the
          // opening line's own delay is zero, and it should land *on* something
          // already playing.
          effects: ["sendQuestion", "startMusic", "armFiller"],
        };
      }
      return stay(state);

    case "thinking":
      if (event.type === "answered") {
        if (event.kind === "text") {
          return {
            state: { ...state, phase: "shaping" },
            effects: ["clearFiller", "shapeAnswer"],
          };
        }
        // An error part is never posted to the speech model: it would be read
        // out as though it were the answer.
        return fail(state, "answerFailed", ["clearFiller", "stopMusic", "stopSpeech"]);
      }
      return stay(state);

    case "shaping":
      if (event.type === "shaped") {
        return {
          state: { ...state, phase: "speaking" },
          // The music runs through `shaping` and stops here, not when the
          // answer arrived: shaping is another one to three seconds of nothing,
          // and going quiet for it would read as the line dropping just before
          // the answer.
          effects: ["stopMusic", event.ok ? "speakShaped" : "speakFallback"],
        };
      }
      return stay(state);

    case "speaking":
      // The dock stays up rather than closing, so the follow-up the answer just
      // prompted can be asked into a surface that is already there.
      if (event.type === "spoken") {
        return { state: { ...state, phase: "lingering" }, effects: [] };
      }
      // Interrupting the reading to ask the next thing. `stopSpeech` is what
      // cuts the audio mid-sentence; without it the previous answer would carry
      // on being read over the new recording.
      if (event.type === "open") return open(state, event.at, ["stopSpeech"]);
      return stay(state);

    case "lingering":
      // Nothing is playing by now -- `spoken` fires when the sound has actually
      // stopped -- so this is an ordinary open, not an interruption.
      return event.type === "open" ? open(state, event.at) : stay(state);
  }
}
