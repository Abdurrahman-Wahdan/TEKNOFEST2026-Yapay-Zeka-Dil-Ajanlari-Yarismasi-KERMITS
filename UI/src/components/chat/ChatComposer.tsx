"use client";

import useMediaQuery from "@mui/material/useMediaQuery";
import { styled, useTheme } from "@mui/material/styles";
import { ArrowUp, Brain, Eye, Mic, Plus, Square } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";

import { VuiBox } from "@/components/vision";
import { useChat } from "@/lib/chat/ChatProvider";
import type { MentionTarget } from "@/lib/chat/types";

import { AttachmentTray } from "./AttachmentTray";
import { MentionMenu, mentionAt } from "./MentionMenu";

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
const SINGLE_ROW_RIGHT_PX = 168;

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
  const theme = useTheme();
  const { status, send, stop, think, setThink, attachments } = useChat();

  /**
   * On a phone the controls always get their own row.
   *
   * The single-row layout needs room for the field *and* four controls. At 375px
   * there is not enough for both: the Think label ended up printed across the
   * placeholder. ChatGPT stacks on mobile for the same reason.
   */
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));

  const [value, setValue] = useState("");
  /** True once the text no longer fits on one line: controls move below. */
  const [multiline, setMultiline] = useState(false);
  /** True while snapdom is working, so the button cannot be pressed twice. */
  const [capturing, setCapturing] = useState(false);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [caret, setCaret] = useState(0);

  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);

  // `submitted` counts as busy: the wait before the first token is exactly when
  // someone wants to cancel, so the stop button has to be live there too.
  const isBusy = status === "streaming" || status === "submitted";
  const hasText = value.trim().length > 0;

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
  const stacked = multiline || narrow;

  /** The `@…` the caret is sitting in, if any. */
  const mention = useMemo(() => mentionAt(value, caret), [value, caret]);
  const mentionTargets: MentionTarget[] = useMemo(() => {
    if (!mention) return [];
    const query = mention.query.toLowerCase();
    return attachments.targets.filter((target) =>
      target.filename.toLowerCase().includes(query),
    );
  }, [mention, attachments.targets]);
  // Only offered once something is staged. `@` with nothing attached is just an
  // at-sign, and a menu saying "nothing to mention" on every one would be noise.
  const mentionOpen = Boolean(mention) && attachments.targets.length > 0;
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
  useEffect(() => {
    const el = fieldRef.current;
    if (!el) return;

    // 1. Would this text fit on one line in the single-row layout? Probed at that
    //    width explicitly, then the override is removed.
    const shellWidth = shellRef.current?.clientWidth ?? 0;
    const probeWidth = Math.max(shellWidth - SINGLE_ROW_LEFT_PX - SINGLE_ROW_RIGHT_PX, 40);
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
  }, [value]);

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
    if (!hasText || isBusy) return;
    send(value);
    setValue("");
    setMultiline(false);
  }, [hasText, isBusy, send, value]);

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
        setMentionIndex(
          (i) =>
            (Math.min(i, mentionTargets.length - 1) + 1) %
            mentionTargets.length,
        );
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
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
        if (target) {
          event.preventDefault();
          pickMention(target);
          return;
        }
      }
      if (event.key === "Escape") {
        event.preventDefault();
        // Closing without picking: step the caret past the `@` so the token no
        // longer parses as an open mention.
        setCaret(-1);
        return;
      }
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

      <ThinkChip
        active={think}
        label={t("think")}
        onToggle={() => setThink(!think)}
      />

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
      >
        <Eye size={20} />
      </RoundButton>

      {/* Kept because the design has it and dictation is a real possibility, but
          disabled: there is no speech-to-text pipeline yet, and a button that
          silently does nothing is worse than one that says it cannot. */}
      <RoundButton label={t("voice")} onClick={() => {}} disabled>
        <Mic size={20} />
      </RoundButton>

      <RoundButton
        label={isBusy ? t("stop") : t("send")}
        onClick={() => (isBusy ? stop() : submit())}
        disabled={!isBusy && !hasText}
        filled
      >
        {isBusy ? (
          <Square size={14} fill="currentColor" />
        ) : (
          <ArrowUp size={18} />
        )}
      </RoundButton>
    </>
  );

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
              pr: stacked ? 2.5 : `${SINGLE_ROW_RIGHT_PX}px`,
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
                placeholder={placeholder ?? ""}
                aria-label={t("title")}
              />

              {!placeholder && !value && <CyclingPlaceholder />}
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

/** One of the composer's round buttons. */
function RoundButton({
  label,
  onClick,
  children,
  disabled,
  filled,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  /** The send/stop button: solid, so it reads as the primary action. */
  filled?: boolean;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={(event: React.MouseEvent) => {
        // The shell focuses the field on click; a button press must not also do
        // that, or pressing stop moves the caret.
        event.stopPropagation();
        onClick();
      }}
      disabled={disabled}
      aria-label={label}
      title={label}
      display="flex"
      alignItems="center"
      justifyContent="center"
      sx={{
        width: 36,
        height: 36,
        flexShrink: 0,
        alignSelf: "center",
        border: "none",
        padding: 0,
        borderRadius: "var(--radius-full)",
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "background-color 150ms ease, color 150ms ease",
        ...(filled
          ? {
              backgroundColor: disabled ? "var(--muted)" : "var(--primary)",
              // Idle, this glyph is the same grey as the attach and mic glyphs
              // beside it: every icon in the composer is one shade, so the row
              // reads as one set of controls. `--text-faint` was a second, dimmer
              // grey and made this button look like a different kind of thing.
              // Enabled it inverts on the brand fill, which is the whole point of
              // the primary action.
              color: disabled ? "var(--control-ink)" : "var(--primary-foreground)",
              "&:hover:not(:disabled)": {
                backgroundColor: "var(--primary-hover)",
              },
            }
          : {
              backgroundColor: "transparent",
              // At rest these are the only thing marking attach and mic as
              // buttons -- there is no border and no fill -- so the glyph has to
              // clear text contrast, which --muted-foreground did not in dark.
              color: "var(--control-ink)",
              "&:hover:not(:disabled)": {
                backgroundColor: "var(--muted)",
                color: "var(--foreground)",
              },
              // No dimming when disabled. The mic is the only disabled control in
              // this row, and fading it to 0.5 made it a visibly lighter grey than
              // the attach and Think glyphs beside it -- the row has to read as one
              // set of controls in one shade.
              //
              // It stays `disabled` regardless: not focusable, not clickable and
              // announced as unavailable, so the honesty is in the semantics rather
              // than in a shade of grey. There is no speech-to-text pipeline yet.
            }),
        "&:focus-visible": {
          outline: "2px solid var(--ring)",
          outlineOffset: 2,
        },
      }}
    >
      {children}
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
function ThinkChip({
  active,
  label,
  onToggle,
}: {
  active: boolean;
  label: string;
  onToggle: () => void;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={(event: React.MouseEvent) => {
        event.stopPropagation();
        onToggle();
      }}
      aria-pressed={active}
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
        <Brain size={18} />
      </span>
      <span>{label}</span>
    </VuiBox>
  );
}
