"use client";

import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from "@mui/material";
import { Mic, ThumbsDown, ThumbsUp } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useMemo, useState } from "react";

import { RoundButton } from "@/components/ui/RoundButton";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";
import { useChat } from "@/lib/chat/ChatProvider";
import type { MessageFeedback } from "@/lib/chat/types";
import { useVoiceSession } from "@/lib/chat/useVoiceSession";

import { VoiceSessionBar } from "./VoiceSessionBar";

export function FeedbackDialog({
  open,
  messageId,
  rating,
  existing,
  onClose,
}: {
  open: boolean;
  messageId: string;
  rating: MessageFeedback["rating"];
  existing?: MessageFeedback;
  onClose: () => void;
}) {
  const t = useTranslations("chat");
  const { saveFeedback } = useChat();
  const [note, setNote] = useState(existing?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const transcribe = useCallback(async (audio: Blob, signal: AbortSignal) => {
    const result = await api.voiceTranscription(audio, signal);
    setNote((current) => [current.trim(), result.text.trim()].filter(Boolean).join(" "));
  }, []);
  const voiceCallbacks = useMemo(() => ({ onEnd: transcribe }), [transcribe]);
  const voice = useVoiceSession(voiceCallbacks);

  const close = useCallback(() => {
    if (saving) return;
    voice.cancel();
    onClose();
  }, [onClose, saving, voice]);

  const submit = useCallback(async () => {
    const cleaned = note.trim();
    if (!cleaned || saving) return;
    setSaving(true);
    setFailed(false);
    try {
      await saveFeedback(messageId, rating, cleaned);
      voice.cancel();
      onClose();
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  }, [messageId, note, onClose, rating, saveFeedback, saving, voice]);

  const voiceBusy = voice.phase !== "idle";
  return (
    <Dialog
      open={open}
      onClose={close}
      className="tf26-agent-popup-owned-overlay"
      fullWidth
      maxWidth="xs"
      PaperProps={{
        sx: {
          borderRadius: "20px",
          border: "1px solid var(--border)",
          background: "var(--card)",
          backgroundImage: "none",
        },
      }}
    >
      <DialogTitle sx={{ pb: 1.25 }}>
        <VuiBox display="flex" alignItems="center" gap={1}>
          {rating === "up" ? <ThumbsUp size={20} /> : <ThumbsDown size={20} />}
          <VuiTypography variant="h6" sx={{ color: "var(--foreground)" }}>
            {rating === "up" ? t("feedbackLikeTitle") : t("feedbackDislikeTitle")}
          </VuiTypography>
        </VuiBox>
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <VuiTypography variant="body2" sx={{ color: "var(--control-ink)" }}>
          {t("feedbackDescription")}
        </VuiTypography>
        <TextField
          autoFocus
          multiline
          minRows={3}
          maxRows={7}
          value={note}
          onChange={(event) => setNote(event.target.value.slice(0, 4000))}
          placeholder={t("feedbackPlaceholder")}
          disabled={saving}
          inputProps={{ maxLength: 4000 }}
          sx={{
            "& .MuiInputBase-input": {
              color: "var(--foreground)",
              WebkitTextFillColor: "var(--foreground)",
              caretColor: "var(--primary)",
            },
            "& textarea.MuiInputBase-input::placeholder": {
              color: "var(--control-ink) !important",
              WebkitTextFillColor: "var(--control-ink) !important",
              opacity: "1 !important",
            },
            "& .MuiInputBase-input.Mui-disabled": {
              color: "var(--control-ink)",
              WebkitTextFillColor: "var(--control-ink)",
            },
          }}
        />
        {voiceBusy ? (
          <VuiBox sx={{ border: "1px solid var(--border)", borderRadius: "16px" }}>
            <VoiceSessionBar
              phase={voice.phase}
              error={voice.error}
              elapsedMs={voice.elapsedMs}
              levels={voice.levels}
              onRetry={() => void voice.start()}
              onMute={voice.toggleMute}
              onEnd={voice.end}
              onCancel={voice.cancel}
            />
          </VuiBox>
        ) : (
          <VuiBox display="flex" alignItems="center" gap={0.75}>
            <RoundButton label={t("feedbackVoice")} onClick={() => void voice.start()}>
              <Mic size={18} />
            </RoundButton>
            <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
              {t("feedbackVoiceHint")}
            </VuiTypography>
          </VuiBox>
        )}
        {failed && <Alert severity="error">{t("feedbackFailed")}</Alert>}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button onClick={close} disabled={saving}>{t("feedbackCancel")}</Button>
        <Button
          variant="contained"
          onClick={() => void submit()}
          disabled={!note.trim() || saving || voice.active}
        >
          {saving ? t("feedbackSaving") : t("feedbackSubmit")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
