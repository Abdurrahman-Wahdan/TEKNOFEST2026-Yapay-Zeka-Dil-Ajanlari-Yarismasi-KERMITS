"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { speakText } from "@/lib/api";

import { speakableText } from "./speech-text";

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

function publish(id: string | null): void {
  speakingId = id;
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

function audioContext(sampleRate: number): AudioContext {
  /*
    One context for the whole app, and it must be built at the model's rate.
    A context at the wrong rate does not fail -- it resamples, and the answer
    comes out at the wrong pitch -- so a rate change (a reconfigured model)
    replaces the context rather than reusing it.
  */
  if (context && context.sampleRate !== sampleRate) {
    void context.close().catch(() => undefined);
    context = null;
  }
  if (!context) {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    context = new Ctor({ sampleRate });
  }
  return context;
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

  const { body, sampleRate } = await speakText(text, abort.signal);
  if (run !== generation) return;

  const ctx = audioContext(sampleRate);
  // Autoplay policy: a context created outside a gesture starts suspended. This
  // call is inside the click that asked for the reading, so it resumes.
  if (ctx.state === "suspended") await ctx.resume();

  const reader = body.getReader();
  let cursor = ctx.currentTime;
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
    (markdown: string) => {
      if (!supported()) return;
      if (speakingId === id) {
        stopSpeaking();
        return;
      }
      // Markdown is turned into prose *here* rather than on the server, because
      // this is the side that knows a table should be read "column: value" and
      // that a code block should not be read at all.
      const spoken = speakableText(markdown);
      if (!spoken) return;

      stopSpeaking();
      generation += 1;
      const run = generation;
      publish(id);
      play(id, spoken, run).catch(() => {
        // A refused reading, a busy model, or a stop. There is nothing here the
        // user can act on, and the button returning to its speaker icon is the
        // signal that it did not play.
        if (run === generation) publish(null);
      });
    },
    [id],
  );

  return { supported: available, speaking: current === id, toggle };
}
