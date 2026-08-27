"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { speakableText, speechChunks } from "./speech-text";

/**
 * Reading an answer out loud, with the browser's own voice.
 *
 * The platform speech synthesiser rather than a server round trip, because there
 * is no TTS endpoint and this needs none: every browser this app supports ships
 * `speechSynthesis`, macOS/Windows/Android all carry a Turkish voice, and the
 * audio starts instantly instead of after a generate-and-download. When a hosted
 * voice arrives it replaces `speak()` below and nothing above it changes -- the
 * button, its states and the text it is handed are already the right shape.
 */

/**
 * Which message is being read, held outside React.
 *
 * `speechSynthesis` is one global queue, so "is this message speaking" is one
 * global fact. Component state would give every action row its own copy of it,
 * and pressing play on a second answer would leave the first one's button
 * showing a stop icon for audio that had already been cancelled.
 */
let speakingId: string | null = null;
/**
 * Invalidates the utterance handlers of a run that has been superseded.
 *
 * `cancel()` does not reliably suppress the pending `onend`/`onerror` of what it
 * cancelled -- browsers disagree on which fires and when -- so a stale handler
 * would otherwise clear the state of the run that replaced it.
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

function supported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

function stopSpeaking(): void {
  generation += 1;
  if (supported()) window.speechSynthesis.cancel();
  publish(null);
}

/**
 * Pick a voice for the page's language.
 *
 * Left to the platform when there is no match. Setting `lang` on the utterance
 * is what a synthesiser needs in order to choose Turkish phonetics; naming a
 * specific voice is only an improvement when one is actually installed, and
 * asking for a missing one gets the default voice reading Turkish as English.
 */
function voiceFor(lang: string): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  const tag = lang.toLowerCase();
  const base = tag.split("-")[0];
  return (
    voices.find((voice) => voice.lang.toLowerCase().replace("_", "-") === tag) ??
    voices.find((voice) => voice.lang.toLowerCase().startsWith(base))
  );
}

function speak(id: string, text: string, lang: string): void {
  const chunks = speechChunks(text);
  if (chunks.length === 0) return;

  generation += 1;
  const run = generation;
  window.speechSynthesis.cancel();
  publish(id);

  const voice = voiceFor(lang);
  chunks.forEach((chunk, index) => {
    const utterance = new SpeechSynthesisUtterance(chunk);
    utterance.lang = lang;
    if (voice) utterance.voice = voice;
    // Only the last chunk ends the run. The queue drains on its own, so an
    // `onend` on every piece would clear the button after the first sentence.
    if (index === chunks.length - 1) {
      utterance.onend = () => {
        if (run === generation) publish(null);
      };
    }
    utterance.onerror = (event) => {
      // `cancel()` reports itself as an error in some browsers. Stopping is not
      // a failure, and it has already published its own state.
      if (run !== generation) return;
      if (event.error === "canceled" || event.error === "interrupted") return;
      publish(null);
    };
    window.speechSynthesis.speak(utterance);
  });
}

/** Nothing to subscribe to: whether the API exists cannot change mid-session. */
const noSubscription = () => () => {};

/**
 * One message's read-aloud control: whether it is speaking, and how to toggle it.
 *
 * Keyed by message id rather than shared, because both things the caller needs
 * are per-message. `speaking` is "is *this* answer the one being read", which is
 * what draws a stop icon on one row while its neighbours still show a speaker.
 * And the unmount cleanup can only be right with an id: speech outlives the DOM,
 * so a row that goes away while it is talking must silence itself -- and a row
 * that goes away while a *different* answer is being read must not.
 *
 * `supported` comes through `useSyncExternalStore` with a `false` server
 * snapshot rather than being read during render. The API is a client-only fact,
 * and a button present in the server's HTML but not in the client's first render
 * is a hydration mismatch.
 */
export function useSpeech(id: string, lang: string) {
  const current = useSyncExternalStore(
    subscribe,
    () => speakingId,
    () => null,
  );
  const available = useSyncExternalStore(noSubscription, supported, () => false);

  useEffect(() => {
    if (!supported()) return;
    /*
      Voices load asynchronously in Chrome: the first `getVoices()` on a cold
      page returns an empty list, and a press inside that window gets the
      default voice reading Turkish as English. Touching it here warms the list
      long before the user reaches the end of an answer.
    */
    window.speechSynthesis.getVoices();
  }, []);

  useEffect(() => {
    /*
      The utterance queue belongs to the window, not to this component, so
      nothing stops it when the popup closes or the route changes -- an answer
      would carry on being read over a page the user has already left.
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
      const spoken = speakableText(text);
      if (!spoken) return;
      speak(id, spoken, lang);
    },
    [id, lang],
  );

  return { supported: available, speaking: current === id, toggle };
}
