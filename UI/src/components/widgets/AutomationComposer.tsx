"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Mic, Send } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useMemo, useRef, useState } from "react";

import { VoiceSessionBar } from "@/components/chat/VoiceSessionBar";
import { ActionButton } from "@/components/ui/ActionButton";
import { RoundButton } from "@/components/ui/RoundButton";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api, ApiError } from "@/lib/api";
import { AUTOMATIONS_KEY, type Weekday } from "@/lib/automations";
import { useVoiceSession } from "@/lib/chat/useVoiceSession";

import {
  FrequencyField,
  EmailDeliveryFields,
  PromptField,
  ScheduleFields,
  toggleDay,
  type Chosen,
} from "./AutomationFields";

import { STATS_KEY } from "./ProfileStats";

/**
 * Describe an automation, and it is created.
 *
 * **Not a fork of `ChatComposer`.** That component is a thousand lines bound to
 * `useChat()`, and its job is sending a turn in a conversation whose state is
 * hoisted above two surfaces. This one has a different job, a different submit
 * target and a time picker, and rendering the real composer here would have put
 * the popup's live conversation on the profile page.
 *
 * What *is* shared is everything that would otherwise drift, and the list grew
 * after the first version shipped with private copies of half of it:
 * `useVoiceSession` owns the microphone, `VoiceSessionBar` draws the recording
 * state, `RoundButton` is the mic, `Dropdown` is the two time selects,
 * `ToggleChip` is the seven day chips and `ActionButton` is the submit — every
 * one of them the same component the rest of the app uses. A fix to permission
 * handling, or to what a control looks like, lands in one place.
 *
 * **The time controls override the agent.** Leaving them on "Asistan seçsin"
 * sends `null` and the drafting agent reads the hour out of the sentence; moving
 * them sends a value, and a value wins. Someone who moved the picker did it
 * after reading their own words, so their reading of "akşam" outranks a model's.
 *
 * One round trip, no confirm step: the row appears in the list below with the
 * schedule that was chosen, and every field on it is editable there. A misread
 * hour costs a click, rather than a confirmation everybody pays every time.
 */

export function AutomationComposer() {
  const t = useTranslations("automations");
  // The mic and the recording bar in this card are the chat composer's, so
  // their labels are too: `chat.voice` already says "voice input" in both
  // locales, and a second key saying the same thing is a second thing to
  // translate and keep in step.
  const tv = useTranslations("chat");
  const queryClient = useQueryClient();

  const [text, setText] = useState("");
  const [hour, setHour] = useState<Chosen>(null);
  const [minute, setMinute] = useState<Chosen>(null);
  const [interval, setInterval] = useState<Chosen>(null);
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [emailFormat, setEmailFormat] = useState<"pdf" | "docx">("pdf");
  /** `null` until the user touches a day chip, so an untouched set is the agent's. */
  const [days, setDays] = useState<Weekday[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement>(null);

  const transcribeRecording = useCallback(
    async (audio: Blob, signal: AbortSignal) => {
      const transcript = await api.voiceTranscription(audio, signal);
      if (!transcript.text) return;
      setText((current) =>
        current ? `${current.trimEnd()} ${transcript.text}` : transcript.text,
      );
      requestAnimationFrame(() => {
        const field = fieldRef.current;
        if (!field) return;
        field.focus();
        field.setSelectionRange(field.value.length, field.value.length);
      });
    },
    [],
  );
  const voiceCallbacks = useMemo(
    () => ({ onEnd: transcribeRecording }),
    [transcribeRecording],
  );
  const voice = useVoiceSession(voiceCallbacks);

  const create = useMutation({
    mutationFn: () =>
      api.describeAutomation({
        text: text.trim(),
        // Only what the user actually set. `undefined` is omitted from the JSON
        // body, which is what leaves the field to the agent — sending `null`
        // explicitly would mean the same thing here, but omitting keeps the
        // request honest about which fields the user touched.
        ...(hour !== null ? { hour } : {}),
        ...(minute !== null ? { minute } : {}),
        ...(days !== null ? { weekdays: days } : {}),
        ...(interval !== null ? { interval_minutes: interval } : {}),
        email_enabled: emailEnabled,
        email_format: emailFormat,
      }),
    onSuccess: () => {
      setText("");
      setHour(null);
      setMinute(null);
      setDays(null);
      setInterval(null);
      setEmailEnabled(false);
      setEmailFormat("pdf");
      setFailed(null);
      queryClient.invalidateQueries({ queryKey: AUTOMATIONS_KEY });
      queryClient.invalidateQueries({ queryKey: STATS_KEY });
    },
    onError: (error) => {
      // A refusal is an answer: the per-user ceiling comes back as 409 with a
      // sentence worth showing, rather than as "something went wrong".
      setFailed(
        error instanceof ApiError && error.isRefusal
          ? error.message
          : t("createFailed"),
      );
    },
  });

  const busy = create.isPending;
  const canSubmit = text.trim().length > 0 && !busy;

  /**
   * The recording surface replaces the card for every phase except idle.
   *
   * `phase !== "idle"`, not `voice.active` — `active` is false for `"error"`,
   * so gating on it meant a denied microphone permission unmounted the bar and
   * put the plain card back with nothing said. Clicking the mic looked like it
   * did nothing at all, which is exactly how it was reported. The error phase is
   * the one the user most needs to see: it carries the reason and the retry.
   * `ChatComposer` has always gated on the phase for this reason.
   */
  if (voice.phase !== "idle") {
    return (
      <VuiBox
        sx={{
          borderRadius: "20px",
          backgroundColor: "var(--card)",
          border: "1px solid var(--ring)",
          boxShadow: "0 0 0 3px color-mix(in srgb, var(--ring) 12%, transparent)",
        }}
      >
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
    );
  }

  return (
    <VuiBox
      display="flex"
      flexDirection="column"
      gap="16px"
      sx={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "20px",
        padding: "16px",
      }}
    >
      <PromptField
        ref={fieldRef}
        value={text}
        disabled={busy}
        placeholder={t("placeholder")}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          // Enter submits, Shift+Enter breaks the line — the same contract
          // the chat composer sets, so the two do not surprise each other.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (canSubmit) create.mutate();
          }
        }}
        aria-label={t("composerTitle")}
      />

      <VuiBox
        display="flex"
        flexDirection="column"
        gap="14px"
        sx={{ borderTop: "1px solid var(--border)", paddingTop: "14px" }}
      >
        <ScheduleFields
          hour={hour}
          minute={minute}
          days={days}
          disabled={busy}
          // Creating: an unset hour means "read it out of my sentence".
          allowAuto
          onHour={(value) => {
            setHour(value);
            // Minutes past an unset hour mean nothing, and leaving a chosen
            // "30" behind while the hour goes back to the agent would send a
            // half-set time. The control disables itself; this keeps the value
            // honest as well as the control.
            if (value === null) setMinute(null);
          }}
          onMinute={setMinute}
          onToggleDay={(day) => setDays((current) => toggleDay(current, day))}
        />

        <FrequencyField
          value={interval}
          onChange={setInterval}
          disabled={busy}
          allowAuto
        />

        <VuiTypography variant="caption" sx={{ color: "var(--text-faint)" }}>
          {t("frequencyHint")}
        </VuiTypography>

        <VuiBox
          display="flex"
          alignItems="center"
          justifyContent="flex-end"
          gap="8px"
          flexWrap="wrap"
        >
          {failed && (
            <VuiTypography
              variant="caption"
              sx={{ color: "var(--destructive)", marginInlineEnd: "auto" }}
            >
              {failed}
            </VuiTypography>
          )}
          <EmailDeliveryFields
            enabled={emailEnabled}
            format={emailFormat}
            onEnabled={setEmailEnabled}
            onFormat={setEmailFormat}
            disabled={busy}
          />
          {/* Mic then submit, the order and the pairing the chat composer's
              control row uses. It sat in the field's top-right corner before,
              as an 18px MUI icon with no hit target -- the one control on this
              page that was not one of ours. */}
          <RoundButton
            label={tv("voice")}
            onClick={() => void voice.start()}
            disabled={busy}
          >
            <Mic size={20} />
          </RoundButton>
          <ActionButton disabled={!canSubmit} onClick={() => create.mutate()}>
            {/* A plain span, not `VuiBox component="span"`: `VuiBox` defaults to
                `color="dark"` and paints it, which would render the glyph and
                the label near-black on the button's own fill. The composer's
                Advanced chip documents the same trap. */}
            <span
              style={{ display: "flex", alignItems: "center", gap: "6px" }}
            >
              <Send size={16} />
              {busy ? t("creating") : t("create")}
            </span>
          </ActionButton>
        </VuiBox>
      </VuiBox>
    </VuiBox>
  );
}
