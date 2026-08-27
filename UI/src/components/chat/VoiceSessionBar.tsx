"use client";

import {
  AlertCircle,
  LoaderCircle,
  Mic,
  MicOff,
  RotateCcw,
  Square,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import {
  formatVoiceElapsed,
  type VoiceSessionError,
  type VoiceSessionPhase,
} from "@/lib/chat/useVoiceSession";

export function VoiceSessionBar({
  phase,
  error,
  elapsedMs,
  levels,
  onRetry,
  onMute,
  onEnd,
  onCancel,
}: {
  phase: VoiceSessionPhase;
  error: VoiceSessionError | null;
  elapsedMs: number;
  levels: number[];
  onRetry: () => void;
  onMute: () => void;
  onEnd: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("chat");

  if (phase === "error") {
    const message =
      error === "permission"
        ? t("voicePermissionDenied")
        : error === "unavailable"
          ? t("voiceUnavailable")
          : error === "transcription"
            ? t("voiceTranscriptionFailed")
          : t("voiceFailed");

    return (
      <VuiBox
        role="alert"
        display="flex"
        alignItems="center"
        gap={1.25}
        sx={{ minHeight: 62, px: 1.5, py: 1 }}
      >
        <VuiBox
          display="flex"
          alignItems="center"
          justifyContent="center"
          sx={{ width: 36, height: 36, color: "var(--destructive)", flexShrink: 0 }}
        >
          <AlertCircle size={20} />
        </VuiBox>
        <VuiTypography
          variant="caption"
          sx={{ color: "var(--foreground)", flex: 1, lineHeight: 1.45 }}
        >
          {message}
        </VuiTypography>
        <VoiceControl label={t("voiceRetry")} onClick={onRetry}>
          <RotateCcw size={18} />
        </VoiceControl>
        <VoiceControl label={t("close")} onClick={onCancel}>
          <X size={19} />
        </VoiceControl>
      </VuiBox>
    );
  }

  const requesting = phase === "requesting";
  const ending = phase === "ending";
  const transcribing = phase === "transcribing";
  const muted = phase === "muted";
  const status = requesting
    ? t("voiceConnecting")
    : ending
      ? t("voiceEnding")
      : transcribing
        ? t("voiceTranscribing")
      : muted
        ? t("voiceMuted")
        : t("voiceListening");

  return (
    <VuiBox
      role="group"
      aria-label={t("voiceSession")}
      display="flex"
      alignItems="center"
      sx={{ minHeight: 62, px: 1.25, py: 0.75 }}
    >
      <VoiceControl label={t("voiceCancel")} onClick={onCancel} disabled={ending}>
        <X size={19} />
      </VoiceControl>

      <VuiBox
        display="flex"
        alignItems="center"
        gap={1.25}
        sx={{ minWidth: 0, flex: 1, px: { xs: 0.75, sm: 1.25 } }}
      >
        <VuiBox sx={{ minWidth: { xs: 76, sm: 116 } }}>
          <VuiTypography
            variant="caption"
            sx={{ display: "block", color: "var(--foreground)", fontWeight: 600 }}
          >
            {status}
          </VuiTypography>
          <VuiTypography
            component="span"
            variant="caption"
            sx={{ color: "var(--control-ink)", fontVariantNumeric: "tabular-nums" }}
          >
            {formatVoiceElapsed(elapsedMs)}
          </VuiTypography>
        </VuiBox>

        <VuiBox
          aria-hidden
          display="flex"
          alignItems="center"
          justifyContent="center"
          gap="3px"
          sx={{ height: 32, minWidth: 0, flex: 1, overflow: "hidden" }}
        >
          {requesting || transcribing ? (
            <LoaderCircle
              size={20}
              style={{ animation: "tf26-voice-spin 900ms linear infinite" }}
            />
          ) : (
            levels.map((level, index) => (
              <VuiBox
                key={index}
                sx={{
                  width: 3,
                  height: `${Math.max(4, Math.round(level * 28))}px`,
                  flexShrink: 0,
                  borderRadius: "var(--radius-full)",
                  backgroundColor: muted
                    ? "var(--control-ink)"
                    : "var(--primary)",
                  opacity: muted ? 0.45 : 0.9,
                  transition: "height 80ms linear, background-color 150ms ease",
                  "@media (prefers-reduced-motion: reduce)": { transition: "none" },
                }}
              />
            ))
          )}
        </VuiBox>
      </VuiBox>

      <VoiceControl
        label={muted ? t("voiceUnmute") : t("voiceMute")}
        onClick={onMute}
        disabled={requesting || ending || transcribing}
      >
        {muted ? <MicOff size={19} /> : <Mic size={19} />}
      </VoiceControl>

      <VoiceControl
        label={t("voiceEnd")}
        onClick={onEnd}
        disabled={requesting || ending || transcribing}
        end
        ml={6}
      >
        <Square size={12} fill="currentColor" />
      </VoiceControl>
    </VuiBox>
  );
}

function VoiceControl({
  label,
  onClick,
  children,
  disabled,
  end,
  ml = 0,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  end?: boolean;
  ml?: number;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      aria-label={label}
      title={label}
      onClick={(event: React.MouseEvent) => {
        event.stopPropagation();
        onClick();
      }}
      disabled={disabled}
      display="flex"
      alignItems="center"
      justifyContent="center"
      sx={{
        width: 36,
        height: 36,
        ml: `${ml}px`,
        flexShrink: 0,
        border: "none",
        borderRadius: "var(--radius-full)",
        padding: 0,
        cursor: disabled ? "not-allowed" : "pointer",
        color: end ? "white" : "var(--control-ink)",
        backgroundColor: end ? "var(--destructive)" : "transparent",
        opacity: disabled ? 0.5 : 1,
        transition: "background-color 150ms ease, opacity 150ms ease",
        "&:hover:not(:disabled)": {
          backgroundColor: end
            ? "color-mix(in srgb, var(--destructive) 86%, black)"
            : "var(--muted)",
        },
        "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
        "@keyframes tf26-voice-spin": { to: { transform: "rotate(360deg)" } },
        "@media (prefers-reduced-motion: reduce)": {
          transition: "none",
          "& svg": { animation: "none !important" },
        },
      }}
    >
      {children}
    </VuiBox>
  );
}
