"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { usePathname } from "@/i18n/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useChat } from "@/lib/chat/ChatProvider";
import { speakableText } from "@/lib/chat/speech-text";
import { primeSpeech, speakAloud, stopAloud } from "@/lib/chat/speech";
import { readPageText } from "@/lib/chat/tools";
import { useVoiceSession } from "@/lib/chat/useVoiceSession";
import type { AttachedContext } from "@/lib/chat/types";

import { answerFromMessages } from "./answer.ts";
import { fillerDelayMs, isOpeningFiller } from "./fillers.ts";
import { duckHoldMusic, startHoldMusic, stopHoldMusic } from "./hold-music.ts";
import { isChatBusy } from "./hotkey.ts";
import {
  INITIAL_VOICE_STATE,
  LINGER_MS,
  MAX_HOLD_MS,
  isVoiceBusy,
  stepVoice,
  type VoiceEvent,
  type VoiceState,
} from "./machine.ts";
import { orbLevelFromBars } from "./orb-level.ts";
import { voicePageContext } from "./page-context.ts";

/**
 * Voice mode's wiring: timers, refs, and the four things it talks to.
 *
 * The decisions all live next door in `machine.ts`, which is pure and tested.
 * What is left here is the part that cannot be: one `useVoiceSession` for the
 * microphone, `speech.ts`'s singleton graph for the output, `useChat` for the
 * question and the answer, and `/voice/response` for the rewrite.
 *
 * `state.runId` is the whole cancellation story. Three async chains are in
 * flight during a turn -- the recorder's stop event, the transcription fetch,
 * and the agent turn -- and none can be killed synchronously, so every
 * continuation reopens by checking that the turn it belongs to is still the
 * current one.
 */

/** The id this surface holds in the shared speech graph. */
const VOICE_MODE_ID = "voice-mode";

/** How long a failure stays on screen before the overlay closes itself. */
const FAILURE_DISMISS_MS = 4_000;

/**
 * How long to wait for `send` to visibly start a turn.
 *
 * `send` is fire-and-forget and bails silently on an empty message, so without
 * this the overlay would sit in `thinking` for ever on a question that was
 * never asked.
 */
const SEND_ACK_MS = 2_000;

/** What the orb does while it is not the user's turn to talk. */
const IDLE_ORB_LEVEL = 0.12;

/**
 * What the orb does while the dock is waiting to be spoken to.
 *
 * Lower than the working level on purpose: `lingering` is the one open phase
 * where nothing is happening, and an orb still churning at it would read as a
 * turn that had hung rather than one that had finished.
 */
const RESTING_ORB_LEVEL = 0.05;

type Turn = {
  question: string;
  contexts: AttachedContext[];
  answer: string;
  spoken: string;
  /** How many holding lines have already gone out this turn. */
  fillerAttempt: number;
  /**
   * How long the transcript was when the question went out.
   *
   * `send` appends the user's turn and an empty assistant turn synchronously,
   * so the list growing past this is proof the turn started. Watching the
   * status for a `busy` edge instead would miss a turn that began and ended
   * between two renders, and report a perfectly good answer as a failure.
   */
  sentAtLength: number;
};

function freshTurn(): Turn {
  return {
    question: "",
    contexts: [],
    answer: "",
    spoken: "",
    fillerAttempt: 0,
    sentAtLength: -1,
  };
}

export function useVoiceMode() {
  const t = useTranslations("voiceMode");
  const pathname = usePathname();
  const { user } = useAuth();
  const { messages, popupOpen, send, status } = useChat();

  const [state, setState] = useState<VoiceState>(INITIAL_VOICE_STATE);
  /*
    The ref is the authoritative copy and the state is the one that renders.
    Two events can land in the same tick -- a keyup arriving while the session
    is still reporting `requesting` is the ordinary case -- and a reducer read
    back out of `useState` would answer the second one with the state from
    before the first.
  */
  const stateRef = useRef<VoiceState>(INITIAL_VOICE_STATE);
  const turnRef = useRef<Turn>(freshTurn());
  const sessionRef = useRef<ReturnType<typeof useVoiceSession> | null>(null);
  const messagesRef = useRef(messages);
  const fillerTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const holdTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lingerTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shapeAbortRef = useRef<AbortController | null>(null);
  const armFillerRef = useRef<() => void>(() => {});
  const runEffectRef = useRef<(effect: string) => void>(() => {});

  const clearTimer = (ref: { current: ReturnType<typeof setTimeout> | null }) => {
    if (ref.current) clearTimeout(ref.current);
    ref.current = null;
  };

  const dispatch = useCallback((event: VoiceEvent) => {
    const step = stepVoice(stateRef.current, event);
    stateRef.current = step.state;
    setState(step.state);
    for (const effect of step.effects) runEffectRef.current(effect);
  }, []);

  const armFiller = useCallback(() => {
    const run = stateRef.current.runId;
    const attempt = turnRef.current.fillerAttempt;
    clearTimer(fillerTimerRef);
    fillerTimerRef.current = setTimeout(() => {
      // Re-checked at fire time, not only at arm time: the answer may have
      // landed during the wait, and the timer is not the only thing that knows.
      // It is the whole guard on the opening line, whose delay is zero -- a
      // question answered inside that tick must not be talked over.
      if (run !== stateRef.current.runId || stateRef.current.phase !== "thinking") return;

      // Two literal keys rather than one resolved from a variable, so
      // `npm run i18n:check` can see both. The opening line acknowledges the
      // question; every one after it is the same holding line.
      const phrase = isOpeningFiller(attempt) ? t("fillerOpening") : t("fillerHolding");
      turnRef.current.fillerAttempt = attempt + 1;
      // Under the line while it is being said, back up after. Unconditionally
      // released in `finally`, including on the abort that a barge-in causes,
      // or the music would stay ducked for the rest of a wait it survived.
      duckHoldMusic(true);
      void speakAloud(VOICE_MODE_ID, phrase)
        .catch(() => undefined)
        .finally(() => {
          // A superseded run does nothing at all -- releasing the duck here
          // would lift it off the turn that replaced this one, leaving the next
          // question's music at full level under its own opening line.
          if (run !== stateRef.current.runId) return;
          duckHoldMusic(false);
          // Armed from the end of the previous phrase rather than on a wall
          // clock, so a slow reading can never have the next one stack on it.
          if (stateRef.current.phase !== "thinking") return;
          armFillerRef.current();
        });
    }, fillerDelayMs(attempt));
  }, [t]);

  useEffect(() => {
    armFillerRef.current = armFiller;
  }, [armFiller]);

  const shapeAnswer = useCallback(async () => {
    const run = stateRef.current.runId;
    const { answer, question } = turnRef.current;
    const controller = new AbortController();
    shapeAbortRef.current = controller;
    try {
      const shaped = await api.voiceResponse(
        { text: answer, question },
        controller.signal,
      );
      if (run !== stateRef.current.runId) return;
      const speech = shaped.speech.trim();
      turnRef.current.spoken = speech || speakableText(answer);
      dispatch({ type: "shaped", ok: Boolean(speech) });
    } catch {
      if (run !== stateRef.current.runId) return;
      // The endpoint being down is not a reason to say nothing. The browser's
      // own converter cannot phrase a table as well, but it also cannot invent
      // a rate, so it is the safe answer rather than a second-class one.
      turnRef.current.spoken = speakableText(answer);
      dispatch({ type: "shaped", ok: false });
    }
  }, [dispatch]);

  const speakAnswer = useCallback(async () => {
    const run = stateRef.current.runId;
    try {
      await speakAloud(VOICE_MODE_ID, turnRef.current.spoken);
      if (run !== stateRef.current.runId) return;
      dispatch({ type: "spoken" });
    } catch (error) {
      if (run !== stateRef.current.runId) return;
      const code = (error as { status?: number } | null)?.status;
      dispatch({ type: "fail", failure: code === 503 ? "busy" : "speechFailed" });
    }
  }, [dispatch]);

  const runEffect = useCallback(
    (effect: string) => {
      switch (effect) {
        case "startSession":
          void sessionRef.current?.start();
          return;
        case "cancelSession":
          sessionRef.current?.cancel();
          clearTimer(holdTimerRef);
          return;
        case "endSession":
          sessionRef.current?.end();
          clearTimer(holdTimerRef);
          return;
        case "sendQuestion": {
          turnRef.current.sentAtLength = messagesRef.current.length;
          send(turnRef.current.question, { contexts: turnRef.current.contexts });
          const run = stateRef.current.runId;
          clearTimer(ackTimerRef);
          ackTimerRef.current = setTimeout(() => {
            if (run !== stateRef.current.runId) return;
            if (stateRef.current.phase !== "thinking") return;
            // Still no turn in the transcript: `send` declined it silently.
            if (messagesRef.current.length > turnRef.current.sentAtLength) return;
            dispatch({ type: "fail", failure: "answerFailed" });
          }, SEND_ACK_MS);
          return;
        }
        case "armFiller":
          armFillerRef.current();
          return;
        case "clearFiller":
          clearTimer(fillerTimerRef);
          clearTimer(ackTimerRef);
          return;
        case "stopSpeech":
          stopAloud();
          return;
        case "startMusic":
          startHoldMusic();
          return;
        case "stopMusic":
          stopHoldMusic();
          return;
        case "shapeAnswer":
          void shapeAnswer();
          return;
        case "speakShaped":
        case "speakFallback":
          void speakAnswer();
          return;
      }
    },
    [dispatch, send, shapeAnswer, speakAnswer],
  );

  useEffect(() => {
    runEffectRef.current = runEffect;
  }, [runEffect]);

  const onEnd = useCallback(
    async (audio: Blob, signal: AbortSignal) => {
      const run = stateRef.current.runId;
      dispatch({ type: "recorderStopped" });
      try {
        const transcript = await api.voiceTranscription(audio, signal);
        if (run !== stateRef.current.runId) return;
        turnRef.current.question = transcript.text.trim();
        dispatch({ type: "transcript", text: turnRef.current.question });
      } catch {
        if (run !== stateRef.current.runId) return;
        dispatch({ type: "fail", failure: "transcriptionFailed" });
      }
      // Resolving here, rather than after the answer, is what releases the
      // microphone: the session returns to idle and tears the capture down as
      // soon as the recording has been transcribed.
    },
    [dispatch],
  );

  const callbacks = useMemo(() => ({ onEnd }), [onEnd]);
  const session = useVoiceSession(callbacks);

  /*
    Through a ref because the wiring is circular: the session needs `onEnd`,
    which dispatches, and the effects a dispatch fires need the session back.
    No dependency array -- the object is rebuilt every render while its
    `start`/`end`/`cancel` stay stable, so there is nothing to compare.
  */
  useEffect(() => {
    sessionRef.current = session;
  });

  // The session's own phase, translated into the machine's vocabulary.
  useEffect(() => {
    if (session.phase === "listening" || session.phase === "muted") {
      dispatch({ type: "listening" });
      return;
    }
    if (session.phase === "error") {
      dispatch({
        type: "fail",
        failure:
          session.error === "permission"
            ? "permissionDenied"
            : session.error === "unavailable"
              ? "unavailable"
              : "transcriptionFailed",
      });
    }
  }, [session.phase, session.error, dispatch]);

  // A key that stuck, or a user who walked away mid-sentence.
  useEffect(() => {
    if (state.phase !== "listening") return;
    const run = state.runId;
    holdTimerRef.current = setTimeout(() => {
      if (run !== stateRef.current.runId) return;
      dispatch({ type: "holdCap" });
    }, MAX_HOLD_MS);
    return () => clearTimer(holdTimerRef);
  }, [state.phase, state.runId, dispatch]);

  /*
    Watching the turn rather than the message id.

    `send` mints a local assistant id and the provider then *replaces* it with
    the server's on the `done` frame, so an id captured at send time stops
    matching part way through. The `ready -> busy -> ready` edge does not move.
  */
  useEffect(() => {
    messagesRef.current = messages;
    if (state.phase !== "thinking") return;
    // The turn has to have appeared before `ready` can mean it finished.
    if (messages.length <= turnRef.current.sentAtLength) return;
    if (isChatBusy(status)) return;
    const answer = answerFromMessages(messages);
    if (answer.kind === "text") turnRef.current.answer = answer.text;
    dispatch({ type: "answered", kind: answer.kind });
  }, [state.phase, status, messages, dispatch]);

  // A failure says its piece and then gets out of the way.
  useEffect(() => {
    if (state.phase !== "failing") return;
    dismissTimerRef.current = setTimeout(
      () => dispatch({ type: "dismiss" }),
      FAILURE_DISMISS_MS,
    );
    return () => clearTimer(dismissTimerRef);
  }, [state.phase, state.runId, dispatch]);

  /*
    The dock waits after the answer, and then closes itself.

    Keyed on `runId` as well as the phase, so the wait belongs to *this* turn:
    a press during it opens the next one, which takes a new id, and the cleanup
    below clears the timer the finished turn had armed rather than letting it
    fire into the middle of the new recording.
  */
  useEffect(() => {
    if (state.phase !== "lingering") return;
    lingerTimerRef.current = setTimeout(() => dispatch({ type: "dismiss" }), LINGER_MS);
    return () => clearTimer(lingerTimerRef);
  }, [state.phase, state.runId, dispatch]);

  const open = useCallback(() => {
    // Read before the overlay goes up, because what travels with the question
    // should be the page the user was looking at when they started talking.
    const outline = readPageText();
    turnRef.current = freshTurn();

    // Inside the gesture, before anything is awaited: the answer arrives a
    // minute later, far outside any activation the browser would accept.
    void primeSpeech().catch(() => undefined);

    dispatch({ type: "open", at: performance.now() });

    const context = voicePageContext(
      outline,
      pathname,
      t("pageLabel"),
      stateRef.current.runId,
    );
    turnRef.current.contexts = context ? [context] : [];
  }, [dispatch, pathname, t]);

  const release = useCallback(() => {
    dispatch({ type: "release", at: performance.now() });
  }, [dispatch]);

  const cancel = useCallback(() => {
    dispatch({ type: "cancel" });
  }, [dispatch]);

  useEffect(
    () => () => {
      clearTimer(fillerTimerRef);
      clearTimer(holdTimerRef);
      clearTimer(dismissTimerRef);
      clearTimer(lingerTimerRef);
      clearTimer(ackTimerRef);
      shapeAbortRef.current?.abort();
      stopAloud();
      // Same reason as `stopAloud`: the audio graph belongs to the window, so a
      // route change mid-wait would leave the loop playing over the next page.
      stopHoldMusic();
    },
    [],
  );

  return {
    phase: state.phase,
    failure: state.failure,
    /** Whether a fresh Space press must be refused right now. */
    busy: isVoiceBusy(state.phase),
    signedIn: Boolean(user),
    popupOpen,
    status,
    pathname,
    /**
     * What the orb reacts to.
     *
     * The recorder's meter while the user is talking; a low constant afterwards,
     * because `levels` is stale once the capture is torn down and an orb frozen
     * mid-answer reads as a hang.
     */
    level:
      state.phase === "listening"
        ? orbLevelFromBars(session.levels)
        : state.phase === "failing" || state.phase === "closed"
          ? 0
          : state.phase === "lingering"
            ? RESTING_ORB_LEVEL
            : IDLE_ORB_LEVEL,
    open,
    release,
    cancel,
  };
}
