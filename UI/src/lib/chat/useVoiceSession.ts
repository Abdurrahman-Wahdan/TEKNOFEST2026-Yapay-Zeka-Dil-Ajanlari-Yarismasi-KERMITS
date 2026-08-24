"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type VoiceSessionPhase =
  | "idle"
  | "requesting"
  | "listening"
  | "muted"
  | "ending"
  | "transcribing"
  | "error";

export type VoiceSessionError =
  | "permission"
  | "unavailable"
  | "transcription"
  | "unknown";

/**
 * Integration points for the future realtime voice transport.
 *
 * The UI owns microphone permission, capture lifetime and audio levels. A voice
 * agent only needs to subscribe to the stream/chunks and close its connection
 * when the session ends; none of that transport policy leaks into the composer.
 */
export interface VoiceSessionCallbacks {
  onStreamReady?: (stream: MediaStream) => void;
  onAudioChunk?: (chunk: Blob) => void;
  onEnd?: (audio: Blob, signal: AbortSignal) => void | Promise<void>;
}

const NO_CALLBACKS: VoiceSessionCallbacks = {};

const BAR_COUNT = 17;
const QUIET_LEVELS = Array.from({ length: BAR_COUNT }, (_, index) =>
  index === Math.floor(BAR_COUNT / 2) ? 0.2 : 0.08,
);

/** Best supported compressed speech format, with a browser-default fallback. */
function recorderOptions(): MediaRecorderOptions | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  const mimeType = [
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ].find((candidate) => MediaRecorder.isTypeSupported(candidate));
  return mimeType ? { mimeType } : undefined;
}

function errorKind(error: unknown): VoiceSessionError {
  if (
    error instanceof DOMException &&
    (error.name === "NotAllowedError" || error.name === "SecurityError")
  ) {
    return "permission";
  }
  return "unknown";
}

/**
 * Own one browser microphone session.
 *
 * MediaRecorder emits small chunks so a realtime endpoint can be attached later,
 * while Web Audio drives the local waveform independently. Today the callbacks
 * are intentionally optional: capture is real, but the bytes stay on-device.
 */
export function useVoiceSession(callbacks: VoiceSessionCallbacks = NO_CALLBACKS) {
  const [phase, setPhase] = useState<VoiceSessionPhase>("idle");
  const [error, setError] = useState<VoiceSessionError | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [levels, setLevels] = useState<number[]>(QUIET_LEVELS);

  const callbacksRef = useRef(callbacks);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const frameRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const transcriptionAbortRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    callbacksRef.current = callbacks;
  }, [callbacks]);

  const releaseResources = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    if (timerRef.current !== null) clearInterval(timerRef.current);
    frameRef.current = null;
    timerRef.current = null;

    sourceRef.current?.disconnect();
    sourceRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  const resetVisuals = useCallback(() => {
    if (!mountedRef.current) return;
    setElapsedMs(0);
    setLevels(QUIET_LEVELS);
  }, []);

  const start = useCallback(async () => {
    const generation = ++generationRef.current;
    transcriptionAbortRef.current?.abort();
    transcriptionAbortRef.current = null;
    chunksRef.current = [];
    releaseResources();
    setError(null);
    setElapsedMs(0);
    setLevels(QUIET_LEVELS);

    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setError("unavailable");
      setPhase("error");
      return;
    }

    setPhase("requesting");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      // A quick cancel while the permission prompt was open invalidates this
      // request. Tracks must still be stopped after the promise resolves.
      if (generation !== generationRef.current || !mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      streamRef.current = stream;
      callbacksRef.current.onStreamReady?.(stream);

      const recorder = new MediaRecorder(stream, recorderOptions());
      const recordedMimeType = recorder.mimeType;
      recorderRef.current = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
          callbacksRef.current.onAudioChunk?.(event.data);
        }
      });
      recorder.addEventListener("stop", () => {
        const chunks = chunksRef.current;
        chunksRef.current = [];
        releaseResources();
        if (generation !== generationRef.current) return;
        const audio = new Blob(chunks, {
          type: recordedMimeType || chunks[0]?.type || "audio/webm",
        });
        if (!audio.size) {
          setError("transcription");
          setPhase("error");
          return;
        }

        const controller = new AbortController();
        transcriptionAbortRef.current = controller;
        setPhase("transcribing");
        void Promise.resolve(callbacksRef.current.onEnd?.(audio, controller.signal))
          .then(() => {
            if (
              mountedRef.current &&
              generation === generationRef.current &&
              !controller.signal.aborted
            ) {
              setPhase("idle");
              resetVisuals();
            }
          })
          .catch(() => {
            if (
              mountedRef.current &&
              generation === generationRef.current &&
              !controller.signal.aborted
            ) {
              setError("transcription");
              setPhase("error");
            }
          })
          .finally(() => {
            if (transcriptionAbortRef.current === controller) {
              transcriptionAbortRef.current = null;
            }
          });
      });
      recorder.start(250);

      const AudioContextClass =
        window.AudioContext ??
        (window as typeof window & { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext;

      if (AudioContextClass) {
        const context = new AudioContextClass();
        audioContextRef.current = context;
        const analyser = context.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.78;
        const source = context.createMediaStreamSource(stream);
        sourceRef.current = source;
        source.connect(analyser);

        const frequencies = new Uint8Array(analyser.frequencyBinCount);
        let previousPaint = 0;
        const paint = (now: number) => {
          if (generation !== generationRef.current) return;
          frameRef.current = requestAnimationFrame(paint);
          // Thirty frames per second is visually fluid without rerendering the
          // whole composer for every analyser tick.
          if (now - previousPaint < 33) return;
          previousPaint = now;
          analyser.getByteFrequencyData(frequencies);
          const next = Array.from({ length: BAR_COUNT }, (_, index) => {
            const startBin = Math.floor((index / BAR_COUNT) * frequencies.length);
            const endBin = Math.max(
              startBin + 1,
              Math.floor(((index + 1) / BAR_COUNT) * frequencies.length),
            );
            let sum = 0;
            for (let bin = startBin; bin < endBin; bin += 1) {
              sum += frequencies[bin] ?? 0;
            }
            return Math.max(0.08, Math.min(sum / (endBin - startBin) / 150, 1));
          });
          setLevels(next);
        };
        frameRef.current = requestAnimationFrame(paint);
      }

      const startedAt = performance.now();
      timerRef.current = setInterval(() => {
        if (mountedRef.current) setElapsedMs(performance.now() - startedAt);
      }, 250);
      setPhase("listening");
    } catch (caught) {
      if (generation !== generationRef.current || !mountedRef.current) return;
      releaseResources();
      setError(errorKind(caught));
      setPhase("error");
    }
  }, [releaseResources, resetVisuals]);

  const toggleMute = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) return;
    const muted = phase !== "muted";
    stream.getAudioTracks().forEach((track) => {
      track.enabled = !muted;
    });
    setPhase(muted ? "muted" : "listening");
    if (muted) setLevels(QUIET_LEVELS);
  }, [phase]);

  const end = useCallback(() => {
    const recorder = recorderRef.current;
    setPhase("ending");
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
      return;
    }
    releaseResources();
    setPhase("idle");
    resetVisuals();
  }, [releaseResources, resetVisuals]);

  const cancel = useCallback(() => {
    generationRef.current += 1;
    transcriptionAbortRef.current?.abort();
    transcriptionAbortRef.current = null;
    chunksRef.current = [];
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    releaseResources();
    setError(null);
    setPhase("idle");
    resetVisuals();
  }, [releaseResources, resetVisuals]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      transcriptionAbortRef.current?.abort();
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      releaseResources();
    };
  }, [releaseResources]);

  return {
    phase,
    error,
    elapsedMs,
    levels,
    start,
    toggleMute,
    end,
    cancel,
    active: phase !== "idle" && phase !== "error",
  };
}

export function formatVoiceElapsed(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}
