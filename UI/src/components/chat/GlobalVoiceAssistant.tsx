"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { VoicePoweredOrb } from "@/components/ui/voice-powered-orb";
import { usePathname } from "@/i18n/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useChat } from "@/lib/chat/ChatProvider";
import {
  hasBlockingVoiceSurface,
  isBlockedVoiceTarget,
  isGlobalVoiceAvailable,
  isGlobalVoiceKey,
} from "@/lib/chat/global-voice";
import { speakableText } from "@/lib/chat/speech-text";
import { primeSpeech, speakOnce, stopSpeech } from "@/lib/chat/speech";
import { useVoiceSession } from "@/lib/chat/useVoiceSession";
import type { AgentMessage } from "@/lib/chat/types";

type GlobalVoicePhase =
  | "idle"
  | "requesting"
  | "listening"
  | "transcribing"
  | "waiting"
  | "formatting"
  | "speaking"
  | "error";

type Completion = {
  index: number;
  resolve: (answer: string) => void;
  reject: (reason: Error) => void;
  cleanup: () => void;
};

const MINIMUM_HOLD_MS = 250;
const PROGRESS_AFTER_MS = 10_000;
const ERROR_VISIBLE_MS = 1_800;
const PROGRESS_SPEECH_ID = "global-voice-progress";
const ANSWER_SPEECH_ID = "global-voice-answer";

function assistantText(message: AgentMessage | undefined): string {
  if (!message || message.role !== "assistant") return "";
  return message.parts
    .flatMap((part) => (part.type === "text" && part.text.trim() ? [part.text] : []))
    .join("\n\n")
    .trim();
}

function assistantError(message: AgentMessage | undefined): string | null {
  if (!message || message.role !== "assistant") return null;
  return message.parts.find((part) => part.type === "error")?.message ?? null;
}

export function GlobalVoiceAssistant() {
  const t = useTranslations("chat.globalVoice");
  const locale = useLocale();
  const pathname = usePathname();
  const { user } = useAuth();
  const { messages, popupOpen, send, status, stop } = useChat();
  const [phase, setPhase] = useState<GlobalVoicePhase>("idle");

  const phaseRef = useRef(phase);
  const voicePhaseRef = useRef("idle");
  const messagesRef = useRef(messages);
  const heldRef = useRef(false);
  const pressedAtRef = useRef(0);
  const completionRef = useRef<Completion | null>(null);
  const errorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const waitForAnswer = useCallback((index: number, signal: AbortSignal) => {
    return new Promise<string>((resolve, reject) => {
      const onAbort = () => {
        completionRef.current = null;
        reject(signal.reason instanceof Error ? signal.reason : new DOMException("Aborted", "AbortError"));
      };
      signal.addEventListener("abort", onAbort, { once: true });
      completionRef.current = {
        index,
        resolve,
        reject,
        cleanup: () => signal.removeEventListener("abort", onAbort),
      };
    });
  }, []);

  useEffect(() => {
    const completion = completionRef.current;
    if (!completion || status !== "ready") return;
    const message = messages[completion.index];
    const answer = assistantText(message);
    const error = assistantError(message);
    if (!answer && !error) return;

    completion.cleanup();
    completionRef.current = null;
    if (error) completion.reject(new Error(error));
    else completion.resolve(answer);
  }, [messages, status]);

  const showErrorThenClose = useCallback(() => {
    setPhase("error");
    if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    errorTimerRef.current = setTimeout(() => setPhase("idle"), ERROR_VISIBLE_MS);
  }, []);

  const transcribeAndAnswer = useCallback(
    async (audio: Blob, signal: AbortSignal) => {
      let progressTimer: ReturnType<typeof setTimeout> | null = null;
      try {
        setPhase("transcribing");
        const transcript = await api.voiceTranscription(audio, signal);
        const question = transcript.text.trim();
        if (!question) throw new Error("The recording contained no speech.");

        const assistantIndex = messagesRef.current.length + 1;
        const answerPromise = waitForAnswer(assistantIndex, signal);
        setPhase("waiting");
        send(question);

        progressTimer = setTimeout(() => {
          if (phaseRef.current !== "waiting" || signal.aborted) return;
          void speakOnce(PROGRESS_SPEECH_ID, t("progressSpeech"), signal).catch(() => undefined);
        }, PROGRESS_AFTER_MS);

        const answer = await answerPromise;
        if (progressTimer) clearTimeout(progressTimer);
        setPhase("formatting");

        let spoken = speakableText(answer);
        try {
          const formatted = await api.voiceResponse(
            { answer, question, locale },
            signal,
          );
          if (formatted.text.trim()) spoken = formatted.text.trim();
        } catch (error) {
          if (signal.aborted) throw error;
          // The deterministic converter is deliberately retained as the
          // availability fallback for this optional second model pass.
        }

        signal.throwIfAborted();
        stopSpeech();
        setPhase("speaking");
        await speakOnce(ANSWER_SPEECH_ID, spoken, signal);
        if (!signal.aborted) setPhase("idle");
      } catch (error) {
        if (progressTimer) clearTimeout(progressTimer);
        if (signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
          setPhase("idle");
          return;
        }
        showErrorThenClose();
      }
    },
    [locale, send, showErrorThenClose, t, waitForAnswer],
  );

  const voiceCallbacks = useMemo(
    () => ({ onEnd: transcribeAndAnswer }),
    [transcribeAndAnswer],
  );
  const voice = useVoiceSession(voiceCallbacks);

  useEffect(() => {
    voicePhaseRef.current = voice.phase;
    const timer = setTimeout(() => {
      if (voice.phase === "requesting") setPhase("requesting");
      else if (voice.phase === "listening" || voice.phase === "muted") setPhase("listening");
      else if (voice.phase === "error") showErrorThenClose();
    }, 0);
    return () => clearTimeout(timer);
  }, [showErrorThenClose, voice.phase]);

  const cancelSession = useCallback(() => {
    heldRef.current = false;
    // `voice.cancel()` aborts the controller passed to `waitForAnswer`, which
    // rejects that promise synchronously. Do it before removing its listener or
    // the async voice turn would remain suspended forever after Escape/blur.
    voice.cancel();
    completionRef.current?.cleanup();
    completionRef.current = null;
    stop();
    stopSpeech();
    setPhase("idle");
  }, [stop, voice]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && phaseRef.current !== "idle") {
        event.preventDefault();
        cancelSession();
        return;
      }
      if (!isGlobalVoiceKey(event) || heldRef.current || phaseRef.current !== "idle") return;
      if (
        !isGlobalVoiceAvailable({
          pathname,
          popupOpen,
          status,
          signedIn: Boolean(user),
        }) ||
        isBlockedVoiceTarget(event.target) ||
        hasBlockingVoiceSurface(document)
      ) return;

      event.preventDefault();
      heldRef.current = true;
      pressedAtRef.current = performance.now();
      stopSpeech();
      void primeSpeech().catch(() => undefined);
      setPhase("requesting");
      void voice.start();
    };

    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space" || !heldRef.current) return;
      event.preventDefault();
      heldRef.current = false;
      const heldFor = performance.now() - pressedAtRef.current;
      if (heldFor < MINIMUM_HOLD_MS || voicePhaseRef.current === "requesting") {
        voice.cancel();
        setPhase("idle");
        return;
      }
      if (voicePhaseRef.current === "listening" || voicePhaseRef.current === "muted") {
        voice.end();
      }
    };

    const onWindowBlur = () => {
      if (phaseRef.current !== "idle") cancelSession();
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onWindowBlur);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onWindowBlur);
    };
  }, [cancelSession, pathname, popupOpen, status, user, voice]);

  useEffect(() => {
    if (phase === "idle") return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [phase]);

  useEffect(() => {
    return () => {
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
      completionRef.current?.cleanup();
      stopSpeech();
    };
  }, []);

  if (phase === "idle") return null;

  const hue = {
    requesting: 20,
    listening: 0,
    transcribing: 35,
    waiting: 80,
    formatting: 115,
    speaking: 175,
    error: -45,
  }[phase];

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={t(phase)}
      data-global-voice-block=""
      className="fixed inset-0 z-[2000] flex items-center justify-center bg-black/20 backdrop-blur-xl"
    >
      <div className="relative h-64 w-64 max-h-[52vw] max-w-[52vw]">
        <VoicePoweredOrb
          enableVoiceControl={false}
          hue={hue}
          className="overflow-hidden rounded-full drop-shadow-[0_0_42px_rgb(94_114_228_/_0.38)]"
        />
      </div>
      <span className="sr-only">{t(phase)}</span>
    </div>
  );
}
