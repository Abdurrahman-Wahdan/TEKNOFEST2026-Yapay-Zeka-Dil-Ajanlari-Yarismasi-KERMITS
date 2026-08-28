"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { speakText } from "@/lib/api";

import { speechErrorKind, type SpeechError } from "./speech-error.ts";

export type { SpeechError };


/**
 * Reading an answer out loud, in the app's own Turkish voice.
 *
 * This used to be the browser's `speechSynthesis`, which was the right thing to
 * ship while there was no model: it worked everywhere and needed no backend. It
 * is now `POST /api/voice/speech`, streaming Trendyol-TTS from the machine the
 * API runs on. The reasons are quality and control -- a Turkish LoRA reading
 * banking prose against whatever generic voice the operating system happened to
 * install -- and the swap is confined to this file. `useSpeech` keeps its
 * signature and `MessageActions` did not change.
 *
 * The audio arrives as raw 16-bit PCM while it is still being generated, and is
 * scheduled into one `AudioContext` piece by piece. First sound lands in about
 * 0.13s and generation runs ~1.9x faster than speech, so once it starts it does
 * not run dry.
 */

/**
 * Which message is being read, held outside React.
 *
 * There is one audio output, so "is this message speaking" is one global fact.
 * Component state would give every action row its own copy of it, and pressing
 * play on a second answer would leave the first one's button showing a stop icon
 * for audio that had already been cancelled.
 */
let speakingId: string | null = null;
/** Cancels the in-flight request and the scheduled audio of a superseded run. */
let controller: AbortController | null = null;
let context: AudioContext | null = null;
let scheduled: AudioBufferSourceNode[] = [];
/**
 * Invalidates the callbacks of a run that has been replaced.
 *
 * The reading is a promise chain over a stream; aborting resolves it eventually,
 * not immediately, so a late `finally` from the previous run would otherwise
 * clear the state belonging to the one that replaced it.
 */
let generation = 0;
const listeners = new Set<() => void>();

/**
 * Why the last reading did not happen, if it did not.
 *
 * Here rather than swallowed, because every way this fails ends with the button
 * back in its idle state and nothing else — which is indistinguishable from the
 * press not registering. The model serving one reader at a time makes "busy" a
 * routine outcome, not an edge case, so it has to be sayable.
 */
/**
 * How far ahead of the clock the first piece is booked.
 *
 * See the note where it is used: the margin between generation and playback is
 * thin, and this is what keeps a hiccup from becoming a gap in a sentence.
 */
const PLAYBACK_LEAD_SECONDS = 0.5;

/** The failure, and the message whose button should report it. */
let speechError: { id: string; kind: SpeechError } | null = null;

function publish(
  id: string | null,
  error: { id: string; kind: SpeechError } | null = null,
): void {
  speakingId = id;
  speechError = error;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Whether this browser can play what the API sends.
 *
 * `AudioContext` rather than `speechSynthesis` now: the voice is the server's,
 * and all the client needs is the ability to play PCM. That is everywhere, but
 * it is still a client-only fact, hence the `false` server snapshot below.
 */
function supported(): boolean {
  return (
    typeof window !== "undefined" &&
    (typeof window.AudioContext !== "undefined" ||
      typeof (window as { webkitAudioContext?: unknown }).webkitAudioContext !==
        "undefined")
  );
}

/**
 * The one output graph, shared with anything else that makes a sound.
 *
 * Exported for `lib/voice/hold-music.ts`, which plays under the wait. A second
 * `AudioContext` would be a second thing to unlock, and `primeSpeech` runs
 * inside the key press precisely so that whatever plays later plays at all --
 * so the hold music has to be on this one or it would be refused by autoplay
 * policy the moment it started more than a gesture away from the keydown.
 */
export function audioContext(): AudioContext {
  /*
    One context for the whole app, built once at the device's own rate and never
    replaced.

    It used to be built at the *model's* rate and thrown away whenever that rate
    changed, to avoid playing the answer at the wrong pitch. That cannot happen:
    `play` books every piece with `createBuffer(1, n, sampleRate)`, and an
    `AudioBuffer` carries its own rate and is resampled into the output device
    correctly. So the rebuild guarded against nothing -- and it cost something
    real, because a replaced context is a *suspended* context, and the one being
    discarded is exactly the one the user's gesture had already unlocked.

    That matters now a reading can start long after the gesture that asked for
    it. In voice mode the answer lands thirty to sixty seconds after the key was
    pressed, far outside any user activation, and a context first resumed there
    is refused by autoplay policy and plays silence.
  */
  if (!context) {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    context = new Ctor();
  }
  return context;
}

/**
 * Unlock the shared output while a real gesture is still in progress.
 *
 * For callers whose audio arrives much later than the key or click that asked
 * for it. Resuming here means the reading that eventually starts plays through
 * a context the browser already trusts.
 */
export async function primeSpeech(): Promise<void> {
  if (!supported()) return;
  const ctx = audioContext();
  if (ctx.state === "suspended") await ctx.resume();
}

function stopSpeaking(): void {
  generation += 1;
  controller?.abort();
  controller = null;
  for (const source of scheduled) {
    try {
      source.stop();
    } catch {
      // Already finished. Stopping a node that has ended throws, and there is
      // nothing to do about it.
    }
  }
  scheduled = [];
  publish(null);
}

/**
 * Play one reading, scheduling each piece as it arrives.
 *
 * The scheduling is the fiddly part. Pieces are generated at roughly 160ms each
 * but not at even sizes or even intervals, so playing each one "now" would leave
 * gaps when generation lagged and overlap them when it ran ahead. Instead each
 * is booked to start where the previous one ends, and the cursor is nudged back
 * to the present whenever it falls behind -- which is what turns a sequence of
 * arriving buffers into continuous speech.
 */
async function play(id: string, text: string, run: number): Promise<void> {
  const abort = new AbortController();
  controller = abort;

  /*
    Unlocked before the request goes out, not after it comes back. The response
    can take seconds -- longer while the model tunnel rotates -- and a resume
    issued after that has left behind the gesture that would have authorised it.
  */
  const ctx = audioContext();
  if (ctx.state === "suspended") await ctx.resume();

  const { body, sampleRate } = await speakText(text, abort.signal);
  if (run !== generation) return;

  const reader = body.getReader();
  /*
    A head start, not a stall. Generation runs only a little faster than playback
    (measured 1.22x on the M1 Max this targets), so scheduling the first piece at
    `currentTime` leaves no slack at all: any hiccup -- another process, a longer
    segment, the model shared with a second reader -- lands as an audible gap
    mid-sentence. Half a second of lead absorbs that, and is short enough that
    the reading still starts about when the button is pressed.
  */
  let cursor = ctx.currentTime + PLAYBACK_LEAD_SECONDS;
  /*
    A chunk can split a 16-bit sample across two reads. Carrying the odd byte
    forward is what keeps the stream aligned -- interpreting it as the low half
    of the next sample instead would shift every sample after it by one byte and
    turn the rest of the reading into noise.
  */
  let carry = new Uint8Array(0);

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (run !== generation) return;
      if (done) break;
      if (!value?.length) continue;

      const merged = new Uint8Array(carry.length + value.length);
      merged.set(carry);
      merged.set(value, carry.length);
      const usable = merged.length - (merged.length % 2);
      carry = merged.slice(usable);
      if (usable === 0) continue;

      const samples = new Int16Array(merged.buffer, merged.byteOffset, usable / 2);
      const buffer = ctx.createBuffer(1, samples.length, sampleRate);
      const channel = buffer.getChannelData(0);
      // 32768, not 32767: the encoder clipped to [-1, 1] before scaling by
      // 32767, so dividing by 32768 cannot overflow and the asymmetry is far
      // below audibility.
      for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 32768;

      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      cursor = Math.max(cursor, ctx.currentTime);
      source.start(cursor);
      cursor += buffer.duration;

      scheduled.push(source);
      source.onended = () => {
        scheduled = scheduled.filter((node) => node !== source);
      };
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }

  if (run !== generation) return;
  /*
    The stream ending means generation finished, not playback. The last pieces
    are still booked ahead on the audio clock, so the button has to stay in its
    stop state until the sound actually stops -- hence waiting out the remaining
    scheduled time rather than publishing null here.
  */
  const remaining = Math.max(0, cursor - ctx.currentTime);
  await new Promise((resolve) => setTimeout(resolve, remaining * 1000));
  if (run === generation) publish(null);
}

/** Stop whatever is being read, from anywhere. */
export function stopAloud(id?: string): void {
  if (id !== undefined && speakingId !== id) return;
  stopSpeaking();
}

/**
 * Read one passage, and resolve when the sound has actually finished.
 *
 * The imperative half of `useSpeech`, for voice mode, which has no rows and no
 * buttons and needs to know when the answer has been said so it can close.
 *
 * Deliberately the *same* singleton graph: starting either one stops the other,
 * so pressing V while a message is being read aloud interrupts that reading
 * rather than talking over it.
 */
export async function speakAloud(
  id: string,
  text: string,
  signal?: AbortSignal,
): Promise<void> {
  const passage = text.trim();
  if (!passage || !supported()) return;
  signal?.throwIfAborted();

  stopSpeaking();
  generation += 1;
  const run = generation;
  publish(id);

  const onAbort = () => {
    if (run === generation) stopSpeaking();
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  try {
    await play(id, passage, run);
    signal?.throwIfAborted();
  } catch (error) {
    /*
      A superseded run is not a failure. `stopSpeaking` aborts the fetch, and the
      rejection that produces would otherwise be reported as the model refusing
      to read something the user had already cancelled.
    */
    if (run !== generation) return;
    publish(null, {
      id,
      kind: speechErrorKind((error as { status?: number } | null)?.status),
    });
    throw error;
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

/** Nothing to subscribe to: whether the API exists cannot change mid-session. */
const noSubscription = () => () => {};

/**
 * One message's read-aloud control: whether it is speaking, and how to toggle it.
 *
 * Keyed by message id rather than shared, because both things the caller needs
 * are per-message. `speaking` is "is *this* answer the one being read", which is
 * what draws a stop icon on one row while its neighbours still show a speaker.
 * And the unmount cleanup can only be right with an id: audio outlives the DOM,
 * so a row that goes away while it is talking must silence itself -- and a row
 * that goes away while a *different* answer is being read must not.
 */
export function useSpeech(id: string, lang: string) {
  void lang; // The voice is the server's now; the model is Turkish either way.

  const current = useSyncExternalStore(
    subscribe,
    () => speakingId,
    () => null,
  );
  const failure = useSyncExternalStore(
    subscribe,
    () => speechError,
    () => null,
  );
  const available = useSyncExternalStore(noSubscription, supported, () => false);

  useEffect(() => {
    /*
      The audio graph belongs to the window, not to this component, so nothing
      stops it when the popup closes or the route changes -- an answer would
      carry on being read over a page the user has already left.
    */
    return () => {
      if (speakingId === id) stopSpeaking();
    };
  }, [id]);

  const toggle = useCallback(
    (text: string) => {
      if (!supported()) return;
      if (speakingId === id) {
        stopSpeaking();
        return;
      }
      // Already inside the click, so the context resumes inside the gesture.
      // `speakAloud` publishes the failure itself; the rejection it re-throws is
      // for callers that have to react to one, and this one does not.
      void speakAloud(id, text).catch(() => undefined);
    },
    [id],
  );

  return {
    supported: available,
    speaking: current === id,
    /** Only this row's failure: another message's is not this button's to report. */
    error: failure?.id === id ? failure.kind : null,
    toggle,
  };
}
