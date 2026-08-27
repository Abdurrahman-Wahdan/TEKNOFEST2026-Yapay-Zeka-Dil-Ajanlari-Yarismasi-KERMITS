"use client";

import useMediaQuery from "@mui/material/useMediaQuery";
import { styled, useTheme } from "@mui/material/styles";
import {
  ArrowUp,
  ArrowRight,
  AudioLines,
  Eye,
  Plus,
  SlidersHorizontal,
  Square,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";

import { RoundButton } from "@/components/ui/RoundButton";
import { VuiBox } from "@/components/vision";
import { api } from "@/lib/api";
import { useChat } from "@/lib/chat/ChatProvider";
import type { MentionTarget } from "@/lib/chat/types";
import { mentionAt } from "@/lib/chat/mention";
import { useVoiceSession } from "@/lib/chat/useVoiceSession";

import { AdvancedMenu } from "./AdvancedMenu";
import { AttachmentTray } from "./AttachmentTray";
import { ContextMenu, ContextRing } from "./ContextRing";
import { MentionMenu } from "./MentionMenu";
import { VoiceSessionBar } from "./VoiceSessionBar";

/**
 * The composer. One of it, in both chat surfaces.
 *
 * It used to take a `variant` -- a `hero` for the page and a `compact` for the
 * popup -- but the two had drifted apart only in ways that were bugs rather than
 * choices: the popup's long native placeholder wrapped onto two lines and its
 * wrapping raised the field's `scrollHeight`, so an *empty* popup composer opened
 * already in the wrapped-text layout. There is one composer now.
 *
 * The layout is ChatGPT's, because that is the behaviour that was asked for and
 * checking it against the real thing settled several details that a static
 * design cannot:
 *
 *  - It is **one row while the text fits on one line** -- attach, field, Think,
 *    mic, send -- and the controls **drop to a row underneath** the moment the
 *    text wraps. The container is a constant 28px radius, so the short state
 *    reads as a pill and the tall state as a rounded box without anything
 *    animating between two different shapes.
 *  - The field grows to ~300px and then scrolls, so pasting five pages of text
 *    gives a tall box with its own scrollbar rather than a composer taller than
 *    the window.
 *  - Think is a quiet inline pill: no border, transparent until active, then a
 *    tinted background with matching text.
 *  - Send is an up-arrow in a filled circle, and becomes a stop square while an
 *    answer is streaming.
 *
 * The two rows are the *same DOM* in both states -- only the controls' position
 * changes, from absolute inside the field row to static below it. Moving the
 * field between two parents instead would unmount and remount it, and typing
 * past the wrap point would lose focus and the caret mid-sentence.
 */

/** Where the field stops growing and starts scrolling. ChatGPT's is ~298px. */
const MAX_FIELD_PX = 300;

/** One line of the field, used to decide whether the text has wrapped yet. */
const LINE_PX = 24;

/**
 * What the single-row layout reserves either side of the field: the attach button
 * on the left, and Think + mic + send on the right.
 *
 * Named because the wrap probe below has to measure against exactly the width
 * these leave behind. When they were inline literals the probe and the padding
 * could drift apart, and a drift here is the difference between a stable layout
 * and one that oscillates.
 */
const SINGLE_ROW_LEFT_PX = 56;

/**
 * The narrowest single-row field worth offering.
 *
 * Above it a sentence fits before wrapping; below it the layout would change
 * within a few words. The page leaves about 544px here and the 420px popup about
 * 196px, so the two land either side of this without it having to know about
 * either surface.
 */
const COMFORTABLE_FIELD_PX = 320;

/**
 * Even spacing in the control row, measured between what you can *see*.
 *
 * A single `gap` looks wrong here because the controls do not paint the same
 * share of their boxes. Measured on the running row: an icon button draws a 20px
 * glyph inside a 36px box, so its ink starts 8px in; the Advanced chip's ink
 * starts at its 10px padding; and Send is a filled 36px disc whose ink starts at
 * 0. With a flat 6px gap the eye and the ring sat 22px apart while the ring and
 * Send sat 14px apart -- the row read as crowded at the right end even though
 * every box gap was identical.
 *
 * So the gap between any two neighbours is the optical distance minus what each
 * one insets its own ink, and the same subtraction sets the distance from the
 * shell's edge.
 */
const OPTICAL_GAP_PX = 22;
const ICON_INK_INSET_PX = 8;
const CHIP_INK_INSET_PX = 10;
const SUGGESTION_INK_INSET_PX = 4.5;
const FILLED_INK_INSET_PX = 0;

const gapBetween = (leftInset: number, rightInset: number) =>
  OPTICAL_GAP_PX - leftInset - rightInset;
// Used only until the right-hand controls have been measured. The real reserve
// is derived from their rendered width below, so adding a control or translating
// the Think label cannot make the field run underneath the buttons again.
const SINGLE_ROW_RIGHT_FALLBACK_PX = 224;
const SINGLE_ROW_CONTROL_CLEARANCE_PX = 20;

/** How long each example placeholder holds before the next one blurs in. */
const PLACEHOLDER_HOLD_MS = 3000;
/** How long the outgoing placeholder takes to blur away. */
const PLACEHOLDER_EXIT_MS = 400;

/**
 * The field, as a real element rather than `VuiBox component="textarea"`.
 *
 * `VuiBox` is `styled(Box)` with the template's decorative ownerState bolted on;
 * it paints layout boxes. Pushing a *controlled* form field through that
 * indirection made typing unreliable -- the value reached the DOM but did not
 * always survive the next render, which for a controlled input means the text
 * silently disappears.
 */
const Field = styled("textarea")({
  width: "100%",
  resize: "none",
  border: "none",
  outline: "none",
  background: "transparent",
  color: "var(--foreground)",
  fontFamily: "inherit",
  fontSize: "0.9375rem",
  lineHeight: `${LINE_PX}px`,
  display: "block",
  padding: 0,
  margin: 0,
  // --control-ink, not --muted-foreground: the palette's muted grey is 3.88:1
  // on the dark --card, so the prompt that tells someone what to type read as
  // disabled text. See the token's note in tailwind.css.
  "&::placeholder": { color: "var(--control-ink)" },
  "&:disabled": { opacity: 0.5, cursor: "not-allowed" },
});

export function ChatComposer({
  autoFocus,
  placeholder,
}: {
  autoFocus?: boolean;
  /**
   * A fixed prompt instead of the cycling examples.
   *
   * The popup passes one. The examples are written to fill a 768px column and are
   * the only thing the full page's empty screen offers, but in a 420px panel a
   * rotating question is noise next to the conversation it sits under -- and a
   * long one wraps, which raises the field's `scrollHeight` and trips the
   * wrapped-text layout on an empty box. So: short and still.
   *
   * Omitted, the examples cycle. That is the only difference between the two
   * surfaces' composers.
   */
  placeholder?: string;
}) {
  const t = useTranslations("chat");
  const locale = useLocale();
  const theme = useTheme();
  const {
    status,
    recommendation,
    send,
    stop,
    think,
    setThink,
    webSearch,
    setWebSearch,
    model,
    setModel,
    attachments,
    mentionTargets: availableMentionTargets,
    serverSessionId,
  } = useChat();

  /**
   * On a phone the controls always get their own row.
   *
   * The single-row layout needs room for the field *and* four controls. At 375px
   * there is not enough for both: the Think label ended up printed across the
   * placeholder. ChatGPT stacks on mobile for the same reason.
   */
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));

  const [value, setValue] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // Wraps the chip and its menu, so a click on either counts as inside.
  const advancedRef = useRef<HTMLDivElement>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const contextRef = useRef<HTMLDivElement>(null);
  /** True once the text no longer fits on one line: controls move below. */
  const [multiline, setMultiline] = useState(false);
  /** True while snapdom is working, so the button cannot be pressed twice. */
  const [capturing, setCapturing] = useState(false);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [caret, setCaret] = useState(0);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const transcribeRecording = useCallback(
    async (audio: Blob, signal: AbortSignal) => {
      const transcript = await api.voiceTranscription(audio, signal);
      if (!transcript.text) return;
      setValue((current) =>
        current ? `${current.trimEnd()} ${transcript.text}` : transcript.text,
      );
      requestAnimationFrame(() => {
        const field = fieldRef.current;
        if (!field) return;
        field.focus();
        field.setSelectionRange(field.value.length, field.value.length);
        setCaret(field.value.length);
      });
    },
    [],
  );
  const voiceCallbacks = useMemo(
    () => ({ onEnd: transcribeRecording }),
    [transcribeRecording],
  );
  const voice = useVoiceSession(voiceCallbacks);

  const fileRef = useRef<HTMLInputElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const rightControlsRef = useRef<HTMLDivElement>(null);
  const [singleRowRightPx, setSingleRowRightPx] = useState(
    SINGLE_ROW_RIGHT_FALLBACK_PX,
  );
  const [shellWidth, setShellWidth] = useState(0);

  /**
   * Reserve exactly the space occupied by the right-hand controls.
   *
   * This used to be a fixed 168px. Once the page-view button joined Think, mic
   * and send, the cluster became wider than that reserve: text painted beneath
   * the icons and the wrap probe made the same late decision. A ResizeObserver
   * also covers translated labels and responsive font changes.
   */
  useLayoutEffect(() => {
    const controls = rightControlsRef.current;
    if (!controls) return;

    const measure = () => {
      const next = Math.ceil(controls.getBoundingClientRect().width)
        + SINGLE_ROW_CONTROL_CLEARANCE_PX;
      setSingleRowRightPx((current) => (current === next ? current : next));
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(controls);
    return () => observer.disconnect();
  }, []);

  /**
   * The shell's own width, which is what decides whether one row is viable.
   *
   * Not the viewport's. The popup is a 420px panel on a full-size screen, so a
   * media query calls it wide while the field it leaves is about 196px -- barely
   * a few words before the text wraps and the layout changes under the cursor.
   * A container measurement covers the popup and the phone with one rule,
   * because they are the same problem.
   */
  useLayoutEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;
    const measure = () => {
      const next = Math.round(shell.clientWidth);
      setShellWidth((current) => (current === next ? current : next));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(shell);
    return () => observer.disconnect();
  }, []);

  // `submitted` counts as busy: the wait before the first token is exactly when
  // someone wants to cancel, so the stop button has to be live there too.
  const isBusy = status === "streaming" || status === "submitted";
  const hasText = value.trim().length > 0;
  const hasAttachment = attachments.prepared.length > 0;
  const canSubmit =
    hasText ||
    hasAttachment ||
    attachments.contexts.length > 0 ||
    attachments.captures.length > 0;

  /**
   * Whether the controls sit on their own row rather than in the field row.
   *
   * Wrapped text, and only wrapped text. Attaching a file does *not* push the
   * controls down: ChatGPT keeps the thumbnail strip on top and leaves `+`, the
   * text, the mic and send on one row below it. An earlier version forced the
   * stacked layout whenever anything was attached, which was a workaround for the
   * real bug -- the floating controls were positioned against the whole shell, so
   * the tray's height dragged them down over the thumbnails. That is fixed by
   * giving them their own positioning context instead.
   */
  /**
   * Too little room for the field to share a row with the controls.
   *
   * Measured against what single-row would actually leave: the shell minus the
   * attach button and the right-hand cluster. Below this the row is not worth
   * having -- the user types a few words, the text wraps, and the controls drop
   * to their own row anyway. Starting stacked skips that reflow.
   */
  const cramped =
    shellWidth > 0 &&
    shellWidth - SINGLE_ROW_LEFT_PX - singleRowRightPx < COMFORTABLE_FIELD_PX;

  const stacked = multiline || narrow || cramped;

  // Dismiss the Advanced menu. `mousedown` rather than `click`, matching the
  // popup shell: the field takes focus on mousedown, so waiting for the click
  // leaves the menu open through the gesture that moved the caret away from it.
  useEffect(() => {
    if (!advancedOpen && !contextOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!advancedRef.current?.contains(target)) setAdvancedOpen(false);
      if (!contextRef.current?.contains(target)) setContextOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setAdvancedOpen(false);
      setContextOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [advancedOpen, contextOpen]);

  /** The `@…` the caret is sitting in, if any. */
  const mention = useMemo(() => mentionAt(value, caret), [value, caret]);
  const mentionTargets: MentionTarget[] = useMemo(() => {
    if (!mention) return [];
    const query = mention.query.trim().toLocaleLowerCase(locale);
    return availableMentionTargets.filter((target) =>
      target.filename.toLocaleLowerCase(locale).includes(query),
    );
  }, [locale, mention, availableMentionTargets]);
  // Offered for staged files and for prepared files sent earlier in this
  // conversation. A picked historical file is resolved by its opaque id on the
  // next request; this is not a filename-only visual shortcut.
  const mentionOpen = Boolean(mention) && availableMentionTargets.length > 0;
  // Clamped at read time rather than reset from an effect: the filtered list
  // shrinks as the query is typed, and an index left pointing past the end would
  // highlight nothing until the next keystroke.
  const activeMention = Math.min(
    mentionIndex,
    Math.max(mentionTargets.length - 1, 0),
  );

  /**
   * Size the field, and decide whether the text has wrapped.
   *
   * The order matters, and the two measurements are deliberately separate.
   *
   * Deciding the wrap from the field's *current* width is a feedback loop: the
   * width depends on `stacked`, and `stacked` depends on whether the text wrapped
   * at that width. Single-row leaves the field
   * `shell - 56 - 168`; stacked leaves it nearly the whole shell. So any text that
   * needs more than the narrow width but less than the wide one wrapped, went
   * stacked, fitted on one line again, went back to single-row, wrapped again --
   * forever. In a 420px popup, where the two widths are about 196px and 380px,
   * that band covers most of a sentence, which is exactly where it showed up.
   *
   * So the decision is measured against the *fixed* single-row width, whatever
   * the field currently is. It can no longer feed back into the width it was
   * measured at: the text wraps once and stays wrapped until it genuinely fits the
   * narrow width again. Only after that is the height set from the real layout.
   */
  // This must run before paint. A normal effect briefly paints the textarea at
  // its new wrapped height while `multiline` is still false, leaving the
  // absolutely-positioned controls over the text (most noticeable under the
  // translated “Gelişmiş” chip). Measuring in a layout effect makes the padding
  // and control position change atomically with the field resize.
  useLayoutEffect(() => {
    const el = fieldRef.current;
    if (!el) return;

    // 1. Would this text fit on one line in the single-row layout? Probed at that
    //    width explicitly, then the override is removed.
    const shellWidth = shellRef.current?.clientWidth ?? 0;
    const probeWidth = Math.max(shellWidth - SINGLE_ROW_LEFT_PX - singleRowRightPx, 40);
    el.style.width = `${probeWidth}px`;
    el.style.height = "auto";
    // A couple of pixels of slack: a single line's scrollHeight is not exactly the
    // line height once the font's own metrics are involved.
    const wrapped = el.scrollHeight > LINE_PX + 4;
    el.style.width = "";

    // 2. Now the real height, at whatever width the field actually has. Reset to
    //    `auto` first so the box can shrink again when text is deleted --
    //    measuring scrollHeight against the current height only ever ratchets
    //    upward.
    el.style.height = "auto";
    const needed = el.scrollHeight;
    el.style.height = `${Math.min(needed, MAX_FIELD_PX)}px`;
    el.style.overflowY = needed > MAX_FIELD_PX ? "auto" : "hidden";

    setMultiline(wrapped);
  }, [singleRowRightPx, value]);

  useEffect(() => {
    if (autoFocus) fieldRef.current?.focus();
  }, [autoFocus]);

  /**
   * Take a picture of the page and stage it.
   *
   * Deliberately not awaited into the send path: capturing a full page takes long
   * enough to notice, and a send that silently stalled behind a screenshot would
   * read as a broken button. It stages like any other attachment and travels on
   * the next message.
   */
  const capture = useCallback(async () => {
    setCapturing(true);
    try {
      const [{ capturePage }, { readPageText }] = await Promise.all([
        import("@/lib/chat/capture"),
        import("@/lib/chat/tools"),
      ]);
      // Both representations, because the button promises the assistant will
      // *see* the page and the two answer different questions: the outline has the
      // exact figures and the current filters, the picture has the layout. Sending
      // only the picture handed the agent numbers it had to read off pixels when
      // they were available as text two lines away.
      const [shot, outline] = [await capturePage(), readPageText()];
      if (!shot) return;
      attachments.addCapture({
        label: `${shot.width}×${shot.height}`,
        dataUrl: shot.dataUrl,
        width: shot.width,
        height: shot.height,
        bytes: shot.bytes,
        // Carried on the capture, not staged as a second chip: one press is one
        // thing the user did, and two attachments would show them the mechanism
        // the eye exists to keep out of the way.
        outline,
      });
    } finally {
      setCapturing(false);
    }
  }, [attachments]);

  const submit = useCallback(() => {
    if (!canSubmit || isBusy || attachments.hasPending || attachments.hasError) return;
    send(value);
    setValue("");
    setMultiline(false);
  }, [attachments.hasError, attachments.hasPending, canSubmit, isBusy, send, value]);

  const acceptRecommendation = useCallback(() => {
    if (!recommendation) return;
    setValue(recommendation);
    requestAnimationFrame(() => {
      const field = fieldRef.current;
      if (!field) return;
      field.focus();
      field.setSelectionRange(recommendation.length, recommendation.length);
      setCaret(recommendation.length);
    });
  }, [recommendation]);

  /** Replace the open `@token` with the picked filename. */
  const pickMention = useCallback(
    (target: MentionTarget) => {
      if (!mention) return;
      const before = value.slice(0, mention.start);
      const after = value.slice(mention.start + 1 + mention.query.length);
      // The filename can contain spaces, so it is wrapped -- otherwise "@bank
      // statement.pdf" reads as a mention of "bank" followed by a stray word.
      const token = `@[${target.filename}] `;
      const next = `${before}${token}${after}`;
      setValue(next);
      const at = before.length + token.length;
      // Put the caret after the inserted token rather than leaving it wherever
      // the replacement happened to land.
      requestAnimationFrame(() => {
        const el = fieldRef.current;
        if (!el) return;
        el.focus();
        el.setSelectionRange(at, at);
        setCaret(at);
      });
    },
    [mention, value],
  );

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // While the mention menu is open it owns the arrows and Enter, or picking a
    // document with the keyboard would send the message instead.
    if (mentionOpen) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (mentionTargets.length === 0) return;
        setMentionIndex(
          (i) =>
            (Math.min(i, mentionTargets.length - 1) + 1) %
            mentionTargets.length,
        );
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        if (mentionTargets.length === 0) return;
        setMentionIndex(
          (i) =>
            (Math.min(i, mentionTargets.length - 1) -
              1 +
              mentionTargets.length) %
            mentionTargets.length,
        );
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        const target = mentionTargets[activeMention];
        // An unmatched @query still owns these keys. Enter must not send the
        // whole chat accidentally just because there is no row to select.
        event.preventDefault();
        if (target) {
          pickMention(target);
          return;
        }
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        // Closing without picking: step the caret past the `@` so the token no
        // longer parses as an open mention.
        setCaret(-1);
        return;
      }
    }

    // An empty composer treats Arrow Right as accepting the recommendation.
    // Once text exists, the key keeps its normal caret-navigation behaviour.
    if (event.key === "ArrowRight" && !value && recommendation) {
      event.preventDefault();
      acceptRecommendation();
      return;
    }

    // Enter sends; Shift+Enter is a newline. The IME check matters for Turkish
    // input: an Enter committing a composition must not also send.
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  /** Keeps the caret position in state so the mention parser can see it. */
  const syncCaret = () => setCaret(fieldRef.current?.selectionStart ?? 0);

  const controls = (
    <>
      <RoundButton label={t("attach")} onClick={() => fileRef.current?.click()}>
        <Plus size={20} />
      </RoundButton>

      <VuiBox sx={{ flex: 1 }} />

      <VuiBox
        ref={rightControlsRef}
        display="flex"
        alignItems="center"
        // No `gap`: each control sets its own left margin, because an even row
        // needs uneven box gaps. See OPTICAL_GAP_PX.
        sx={{
          flexShrink: 0,
          // Send is a filled disc that runs to its box edge, so it would sit
          // 8px nearer the shell's edge than the attach glyph sits to the other.
          mr: `${ICON_INK_INSET_PX}px`,
        }}
      >
        {/* Deliberately *not* `position: relative`.
            The popovers below are absolutely positioned, so they resolve against
            the nearest positioned ancestor -- the field row, which spans the
            composer. Anchored to this 111px chip instead, `right: 0` sent a
            288px menu 288px leftward from the chip's right edge: fine on the
            page, and straight off the left edge of the 420px popup, where it
            clipped the model names. The ref is still here for click-outside. */}
        <VuiBox ref={advancedRef} sx={{ flexShrink: 0 }}>
          <AdvancedChip
            open={advancedOpen}
            // Tinted whenever a setting is off-default, not only while the menu
            // is open. The Think chip showed that state on its face; folding it
            // into a menu must not cost the user the ability to see it.
            changed={think || webSearch || model !== undefined}
            label={t("advanced")}
            onToggle={() => setAdvancedOpen(!advancedOpen)}
          />
          {advancedOpen && (
            <AdvancedMenu
              think={think}
              webSearch={webSearch}
              model={model}
              onThink={setThink}
              onWebSearch={setWebSearch}
              onModel={setModel}
            />
          )}
        </VuiBox>

        {/* "Look at this page", not "take a screenshot". The user is asking the
            assistant to see what they see; whether that happens by photographing
            the page or reading its markup is ours to decide, and naming the
            mechanism only invites questions about it. The agent can ask for the
            same thing itself -- this is the affordance for a user who already
            knows they want it. */}
        <RoundButton
          label={capturing ? t("capturing") : t("capture")}
          onClick={capture}
          disabled={capturing}
          ml={gapBetween(CHIP_INK_INSET_PX, ICON_INK_INSET_PX)}
        >
          <Eye size={20} />
        </RoundButton>

        {/* Kept because the design has it and dictation is a real possibility, but
            disabled: there is no speech-to-text pipeline yet, and a button that
            silently does nothing is worse than one that says it cannot. */}
        {/* Where the mic was. That button had been disabled since it was added
            -- there is no speech-to-text pipeline -- so the row was spending a
            slot on a control that did nothing. */}
        {/* Static for the same reason as the Advanced wrapper above. */}
        <VuiBox
          ref={contextRef}
          sx={{
            flexShrink: 0,
            ml: `${gapBetween(ICON_INK_INSET_PX, ICON_INK_INSET_PX)}px`,
          }}
        >
          <ContextRing
            sessionId={serverSessionId}
            open={contextOpen}
            onToggle={() => setContextOpen(!contextOpen)}
          />
          {contextOpen && serverSessionId && (
            <ContextMenu
              sessionId={serverSessionId}
              onCompacted={() => setContextOpen(false)}
            />
          )}
        </VuiBox>

        <RoundButton
          label={isBusy ? t("stop") : canSubmit ? t("send") : t("voiceStart")}
          onClick={() => (isBusy ? stop() : canSubmit ? submit() : void voice.start())}
          disabled={!isBusy && canSubmit && (attachments.hasPending || attachments.hasError)}
          filled
          ml={gapBetween(ICON_INK_INSET_PX, FILLED_INK_INSET_PX)}
        >
          {isBusy ? (
            <Square size={14} fill="currentColor" />
          ) : canSubmit ? (
            <ArrowUp size={18} />
          ) : (
            <AudioLines size={20} />
          )}
        </RoundButton>
      </VuiBox>
    </>
  );

  if (voice.phase !== "idle") {
    return (
      <VuiBox sx={{ position: "relative", width: "100%", maxWidth: 768, mx: "auto" }}>
        <VuiBox
          data-no-quote=""
          sx={{
            position: "relative",
            borderRadius: "28px",
            backgroundColor: "var(--card)",
            border: "1px solid var(--ring)",
            boxShadow:
              "0 0 0 3px color-mix(in srgb, var(--ring) 12%, transparent)",
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
      </VuiBox>
    );
  }

  return (
    <VuiBox
      sx={{
        position: "relative",
        width: "100%",
        // Capped for the full page's wide column; inside the 420px popup the panel
        // is narrower than this anyway, so one value serves both.
        maxWidth: 768,
        mx: "auto",
      }}
    >
      {mentionOpen && (
        <MentionMenu
          targets={mentionTargets}
          activeIndex={activeMention}
          onPick={pickMention}
        />
      )}

      {/* The picker itself. Hidden rather than styled: a native file input cannot
          be made to look like anything, so the `+` button clicks it instead. */}
      <VuiBox
        component="input"
        ref={fileRef}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,.txt,.md,.markdown,.pdf,.docx"
        onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
          if (event.target.files?.length) attachments.add(event.target.files);
          // Cleared so picking the same file twice in a row still fires a change.
          event.target.value = "";
        }}
        sx={{ display: "none" }}
      />

      <VuiBox
        // The width the wrap probe measures against. It is this box, not the outer
        // wrapper, because this is what the field's padding is subtracted from.
        ref={shellRef}
        // Selecting inside the composer is the user editing their own question,
        // so `SelectionReply` leaves it alone -- a floating button there would
        // cover the words being worked on.
        data-no-quote=""
        onClick={() => fieldRef.current?.focus()}
        sx={{
          position: "relative",
          cursor: "text",
          // One radius for both states. At 56px tall this reads as a pill and at
          // 300px as a rounded box, so nothing has to animate between shapes.
          borderRadius: "28px",
          backgroundColor: "var(--card)",
          border: "1px solid var(--border)",
          transition: "border-color 150ms ease",
          "&:focus-within": { borderColor: "var(--ring)" },
        }}
      >
        <AttachmentTray
          attachments={{
            images: attachments.images,
            files: attachments.files,
            contexts: attachments.contexts,
            captures: attachments.captures,
            onRemoveImage: attachments.removeImage,
            onRemoveFile: attachments.removeFile,
            onRemoveContext: attachments.removeContext,
            onRemoveCapture: attachments.removeCapture,
            hasPending: attachments.hasPending,
            hasError: attachments.hasError,
          }}
        />

        {/*
          The input area: the field row and the controls, and the positioning
          context the floating controls resolve against.

          It exists so that context is *not* the whole shell. The attachment tray
          is a sibling above it, and with the controls anchored to the shell the
          tray's height was included in `top: 0; bottom: 0` -- so centring them
          vertically pushed them down over the thumbnails and buried the attach
          button under one. Anchored here they centre on the field row alone, and
          the tray can be any height without moving them.
        */}
        <VuiBox sx={{ position: "relative" }}>
          <VuiBox
            sx={{
              pt: 2,
              pb: stacked ? 1 : 2,
              // Single-line: the controls sit *in* this row — the attach button on
              // the left, the rest on the right — so the text has to clear both
              // ends. Multiline: they are on their own row below and the text gets
              // the full width.
              pl: stacked ? 2.5 : `${SINGLE_ROW_LEFT_PX}px`,
              pr: stacked ? 2.5 : `${singleRowRightPx}px`,
              transition: "padding 200ms ease",
            }}
          >
            {/*
            The positioning context for the animated placeholder is this box, not
            the padded row above it.

            An absolutely-positioned child is placed against its ancestor's
            *padding box*, so anchoring the placeholder to the row meant `left: 0`
            landed inside the row's 56px left padding — putting the example
            question underneath the attach button. Wrapping the field in its own
            relative box makes the placeholder's `inset: 0` mean exactly "over the
            field", whatever the row's padding happens to be.
          */}
            <VuiBox sx={{ position: "relative" }}>
              <Field
                ref={fieldRef}
                rows={1}
                value={value}
                onChange={(event) => {
                  setValue(event.target.value);
                  setCaret(event.target.selectionStart ?? 0);
                  // Back to the top of the list as the query changes.
                  setMentionIndex(0);
                }}
                onKeyDown={onKeyDown}
                onKeyUp={syncCaret}
                onClick={syncCaret}
                onSelect={syncCaret}
                // Always empty: the animated placeholder below is painted over
                // this field, so a native one would show through it. It was also
                // what inflated the popup's box -- a placeholder long enough to
                // wrap raises a textarea's `scrollHeight`, which tripped the
                // wrapped-text layout on an empty field.
                placeholder={recommendation ? "" : placeholder ?? ""}
                aria-label={t("title")}
              />

              {!value &&
                (recommendation ? (
                  <RecommendationPlaceholder
                    text={recommendation}
                    label={t("acceptRecommendation")}
                    onAccept={acceptRecommendation}
                  />
                ) : (
                  !placeholder && <CyclingPlaceholder />
                ))}
            </VuiBox>
          </VuiBox>

          <VuiBox
            display="flex"
            alignItems="center"
            gap={0.75}
            sx={
              stacked
                ? { px: 1.25, pb: 1.25 }
                : {
                    // Same nodes, different position — see the note at the top of
                    // this file about why they are not moved in the DOM.
                    //
                    // Pinned to *both* edges, not just the right: this is a flex row
                    // with a spacer between the attach button and the rest, so it
                    // has to span the full width for the spacer to push them apart.
                    // Anchored on `right` alone the row shrank to its contents and
                    // dragged the attach button over to the right-hand cluster.
                    position: "absolute",
                    left: 10,
                    right: 10,
                    bottom: 0,
                    top: 0,
                    px: 0,
                    pb: 0,
                  }
            }
          >
            {controls}
          </VuiBox>
        </VuiBox>
      </VuiBox>
    </VuiBox>
  );
}

/**
 * The example questions, blurring in a letter at a time.
 *
 * The keyframes live in `src/styles/tailwind.css` beside the app's other
 * entrance animations and the stagger is an inline `animation-delay` per letter,
 * so there is no animation library involved and the whole thing is already
 * covered by that file's `prefers-reduced-motion` block.
 */
function CyclingPlaceholder() {
  const t = useTranslations("chat");
  // `raw` because this key is a list, not a string.
  const phrases = t.raw("placeholders") as string[];

  const [index, setIndex] = useState(0);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    if (phrases.length < 2) return;

    let swap: ReturnType<typeof setTimeout>;
    const hold = setTimeout(() => {
      setLeaving(true);
      swap = setTimeout(() => {
        setIndex((prev) => (prev + 1) % phrases.length);
        setLeaving(false);
      }, PLACEHOLDER_EXIT_MS);
    }, PLACEHOLDER_HOLD_MS);

    // Both timers, or a route change mid-exit leaves one to set state on an
    // unmounted component.
    return () => {
      clearTimeout(hold);
      clearTimeout(swap);
    };
  }, [index, phrases.length]);

  if (phrases.length === 0) return null;
  const phrase = phrases[index] ?? "";

  return (
    <VuiBox
      aria-hidden
      sx={{
        // Exactly over the field — see the note at the call site about why this
        // is `inset` on a wrapper rather than hand-matched offsets.
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        // The same ink as the native placeholder it stands in for -- these two
        // must be indistinguishable, so they read the same token.
        color: "var(--control-ink)",
        fontSize: "0.9375rem",
        lineHeight: `${LINE_PX}px`,
        whiteSpace: "nowrap",
        overflow: "hidden",
      }}
    >
      {/* Keyed by phrase index so React replaces the spans outright and the
          animation restarts, rather than diffing letter-for-letter. */}
      {Array.from(phrase).map((char, i) => (
        <VuiBox
          key={`${index}-${i}`}
          component="span"
          className={leaving ? "animate-letter-out" : "animate-letter-in"}
          sx={{ color: "inherit" }}
          style={
            {
              display: "inline-block",
              // Reversed on the way out so the phrase leaves from its start, the
              // way it arrived.
              animationDelay: `${(leaving ? phrase.length - i : i) * 12}ms`,
            } as CSSProperties
          }
        >
          {char === " " ? " " : char}
        </VuiBox>
      ))}
    </VuiBox>
  );
}

function RecommendationPlaceholder({
  text,
  label,
  onAccept,
}: {
  text: string;
  label: string;
  onAccept: () => void;
}) {
  return (
    <VuiBox
      sx={{
        position: "absolute",
        inset: 0,
        zIndex: 2,
        display: "flex",
        alignItems: "flex-start",
        minWidth: 0,
        pointerEvents: "none",
        color: "var(--composer-suggestion-ink)",
        fontSize: "0.9375rem",
        lineHeight: `${LINE_PX}px`,
      }}
    >
      <VuiBox
        aria-hidden
        sx={{
          flex: 1,
          minWidth: 0,
          // VuiBox supplies its own theme-derived text colour. Explicitly
          // inherit here or it overwrites the accessible recommendation ink
          // with light-on-light / dark-on-dark when the theme changes.
          color: "inherit",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          pointerEvents: "none",
        }}
      >
        {text}
      </VuiBox>
      <VuiBox
        component="button"
        type="button"
        aria-label={label}
        title={`${label} (→)`}
        onMouseDown={(event: React.MouseEvent<HTMLButtonElement>) => {
          // Accept before the composer's shell moves focus back to the textarea.
          // The click handler remains for keyboard activation, which has no
          // preceding mouse event.
          event.preventDefault();
          event.stopPropagation();
          onAccept();
        }}
        onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
          event.stopPropagation();
          onAccept();
        }}
        sx={{
          flexShrink: 0,
          width: 24,
          height: 24,
          ml: 0.75,
          // This button sits in the field overlay rather than in the control
          // row, so its right-side gap must be reserved explicitly. Match the
          // optical distance used between the icon and the Advanced chip.
          mr: `${gapBetween(SUGGESTION_INK_INSET_PX, CHIP_INK_INSET_PX)}px`,
          p: 0,
          display: "inline-grid",
          placeItems: "center",
          border: 0,
          borderRadius: "999px",
          color: "var(--composer-suggestion-ink)",
          backgroundColor:
            "color-mix(in srgb, var(--composer-suggestion-ink) 10%, transparent)",
          cursor: "pointer",
          pointerEvents: "auto",
          "&:hover": {
            color: "var(--foreground)",
            backgroundColor:
              "color-mix(in srgb, var(--foreground) 12%, transparent)",
          },
          "&:focus-visible": {
            outline: "2px solid var(--ring)",
            outlineOffset: 2,
          },
        }}
      >
        <ArrowRight size={15} />
      </VuiBox>
    </VuiBox>
  );
}

/**
 * The Think toggle.
 *
 * Borderless, and transparent until it is on -- the earlier version outlined it
 * and filled it with `--info-subtle`, which in the dark palette is `--accent`
 * (#061622, all but black), so an active toggle was indistinguishable from an
 * inactive one. Checked against ChatGPT's, which tints the background and matches
 * the text to it, with no border in either state.
 */
function AdvancedChip({
  open,
  changed,
  label,
  onToggle,
}: {
  /** Whether the menu is showing. */
  open: boolean;
  /** Whether anything inside the menu is off its default. */
  changed: boolean;
  label: string;
  onToggle: () => void;
}) {
  // One tinted state, two reasons to be in it: the menu is open, or a setting
  // inside it is non-default. Both mean "there is something to look at here".
  const active = open || changed;
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={(event: React.MouseEvent) => {
        event.stopPropagation();
        onToggle();
      }}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={label}
      title={label}
      display="flex"
      alignItems="center"
      gap={0.75}
      px={1.25}
      sx={{
        height: 36,
        flexShrink: 0,
        alignSelf: "center",
        cursor: "pointer",
        whiteSpace: "nowrap",
        border: "none",
        borderRadius: "var(--radius-full)",
        fontSize: "0.875rem",
        fontWeight: "var(--weight-medium)",
        fontFamily: "inherit",
        transition: "background-color 150ms ease, color 150ms ease",
        ...(active
          ? {
              // A real tint of the brand colour over the surface, not the
              // near-black `--info-subtle`.
              backgroundColor:
                "color-mix(in srgb, var(--primary) 22%, var(--card))",
              // --primary-strong, not --primary. --primary is a fill colour:
              // printed as text over a 22% tint of itself it measured 2.4:1 in
              // light mode -- light blue on pale blue, the state that made this
              // chip unreadable the moment it was switched on. The strong step
              // is the same hue darkened (light) or lifted (dark) until the
              // *pair* clears 4.5:1: 5.22:1 light, 5.33:1 dark, so the 22% tint
              // survives unchanged.
              color: "var(--primary-strong)",
            }
          : {
              backgroundColor: "transparent",
              // Off is the chip's normal state, and its label is the only thing
              // saying what the toggle does, so it is a control label rather
              // than decoration.
              color: "var(--control-ink)",
              "&:hover": {
                backgroundColor: "var(--muted)",
                color: "var(--foreground)",
              },
            }),
        "&:focus-visible": {
          outline: "2px solid var(--ring)",
          outlineOffset: 2,
        },
      }}
    >
      {/*
        Plain spans, not `VuiBox component="span"`.

        `VuiBox` defaults to `color="dark"` and paints it, so wrapping the glyph
        and the label in one overrode the button's own colour and rendered both
        near-black (#242628) on a dark composer -- while the attach and mic
        buttons, which put their `<svg>` straight in the button, inherited
        correctly. That mismatch is exactly what made this toggle look a different
        shade from every other control in the row. `VuiBox` is for layout boxes;
        inline content that must inherit colour should not go through it.
      */}
      <span style={{ display: "flex", flexShrink: 0 }}>
        <SlidersHorizontal size={18} />
      </span>
      <span>{label}</span>
    </VuiBox>
  );
}
