"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import { VoicePoweredOrb } from "@/components/ui/voice-powered-orb";
import {
  VOICE_ACTIVATES_SELECTOR,
  VOICE_BLOCKING_SELECTOR,
  VOICE_DOCK_SELECTOR,
  VOICE_TEXT_ENTRY_SELECTOR,
  shouldOpenVoiceMode,
} from "@/lib/voice/hotkey.ts";
import { useVoiceMode } from "@/lib/voice/useVoiceMode.ts";

/**
 * Hold Space anywhere on the dashboard and ask out loud.
 *
 * One listener for the whole app rather than one per page, for the same reason
 * `SelectionReply` and `ReportToasts` are mounted beside it: the point of this
 * is that it works on the page you happen to be on, so it cannot live on any of
 * them.
 *
 * **It is a dock, not a dialog.** It rises from the bottom edge, takes a strip
 * of the screen and nothing more, and leaves the page behind it visible, legible
 * and clickable the whole time -- no backdrop, no blur, no scroll lock, no focus
 * trap. That is not decoration: the question is nearly always *about* what is on
 * screen ("bunlardan hangisi daha iyi?"), and the previous full-screen overlay
 * covered the one thing the user was asking about while they asked about it.
 * Only the dock itself takes the pointer; every click either side of it reaches
 * the page underneath.
 *
 * There is still no transcript and no status text, because there is nothing to
 * read -- the phase is carried by the orb's colour for anyone watching and by a
 * live region for anyone not. Words appear in exactly two places: a failure,
 * which is the one thing the voice cannot say for itself, and the hint under
 * `lingering`, which is where the user has to be told the dock is still theirs.
 */

/** Above the toast stack, which is the current top of the z-index ladder. */
const VOICE_DOCK_Z = 1500;

/** Matches `AgentPopup`'s inset, so the two agree at the bottom edge. */
const EDGE = "2rem";

/**
 * How far the dock keeps off the assistant launcher on a phone.
 *
 * The launcher is a 3.5rem circle 2rem in from the right corner, and on a
 * narrow screen a centred dock lands straight on top of it. Padding the
 * container rather than shifting the dock is what keeps this to one line: the
 * dock stays centred in whatever space is left, which on a wide screen is the
 * whole width.
 */
const LAUNCHER_CLEARANCE = "6.5rem";

/**
 * How big the orb is, and so how tall the dock is.
 *
 * The dock is the whole of what the user sees during a turn, and the orb is the
 * whole of what the dock says -- there is no transcript and no status line, so
 * the phase colour and the level animation carry it alone. It has to be big
 * enough to read at a glance from across a desk, which 56px was not. Everything
 * else in the dock sizes off this.
 */
const ORB_SIZE = 76;

/** The orb's colour per phase, so a glance says which part of the turn this is. */
const PHASE_HUE: Record<string, number> = {
  arming: 20,
  listening: 0,
  stopping: 20,
  transcribing: 35,
  thinking: 80,
  shaping: 115,
  speaking: 175,
  lingering: 205,
  failing: -45,
};

function isVisible(element: Element): boolean {
  // An always-mounted but hidden dialog must not switch the feature off for
  // good. `checkVisibility` is not everywhere yet, so its absence means "trust
  // that it is showing", which is the safe direction: we decline to open.
  const check = (element as Element & { checkVisibility?: () => boolean })
    .checkVisibility;
  return check ? check.call(element) : true;
}

/**
 * A dialog, menu or listbox is open somewhere -- but not one of ours.
 *
 * The dock has to be excluded by hand, and this is the line that makes barging
 * in work at all. The probe runs over the whole document, so while the dock is
 * up it is itself a match, and a Space pressed to cut the assistant off would
 * be refused as "a surface owns the screen" -- by the very surface asking the
 * question. It used to carry `role="dialog"` and `data-voice-block` and did
 * exactly that.
 */
function hasBlockingSurface(): boolean {
  return Array.from(document.querySelectorAll(VOICE_BLOCKING_SELECTOR)).some(
    (element) => !element.closest(VOICE_DOCK_SELECTOR) && isVisible(element),
  );
}

export function VoiceMode() {
  const t = useTranslations("voiceMode");
  const voice = useVoiceMode();
  const heldRef = useRef(false);

  const { phase, failure, busy, signedIn, popupOpen, status, pathname, open, release, cancel } =
    voice;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && phase !== "closed") {
        event.preventDefault();
        heldRef.current = false;
        cancel();
        return;
      }

      const target = event.target;
      const element = target instanceof Element ? target : null;
      const accepted = shouldOpenVoiceMode(event, {
        pathname,
        inTextEntry: Boolean(element?.closest(VOICE_TEXT_ENTRY_SELECTOR)),
        activatesOnSpace: Boolean(element?.closest(VOICE_ACTIVATES_SELECTOR)),
        blockingSurface: hasBlockingSurface(),
        popupOpen,
        status,
        signedIn,
        voiceBusy: busy,
      });
      if (!accepted) return;

      // Not optional: without it the page scrolls, and a space landing on a
      // focused control would fire its click on the way back up.
      event.preventDefault();
      heldRef.current = true;
      open();
    };

    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space" || !heldRef.current) return;
      event.preventDefault();
      heldRef.current = false;
      release();
    };

    /*
      Leaving the window ends the turn.

      A key-up delivered to another window never reaches us, so without this a
      tab switch mid-sentence would leave the recorder running and the dock up
      behind it.
    */
    const onLeave = () => {
      if (!heldRef.current && phase === "closed") return;
      heldRef.current = false;
      cancel();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") onLeave();
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onLeave);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onLeave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [busy, cancel, open, pathname, phase, popupOpen, release, signedIn, status]);

  if (phase === "closed") return null;

  const caption = failure ? t(failure) : phase === "lingering" ? t("hint") : null;

  return (
    /*
      The container spans the width and catches nothing. `pointerEvents: none`
      is what keeps the page usable underneath: without it this strip would
      swallow every click across the bottom of the window, including the ones
      meant for the table the question was about.
    */
    <VuiBox
      sx={{
        position: "fixed",
        left: 0,
        right: 0,
        bottom: { xs: "1rem", sm: EDGE },
        zIndex: VOICE_DOCK_Z,
        display: "flex",
        justifyContent: "center",
        pl: { xs: 2, sm: 3 },
        pr: { xs: LAUNCHER_CLEARANCE, sm: 3 },
        pointerEvents: "none",
      }}
    >
      <VuiBox
        data-voice-dock=""
        role="group"
        aria-label={t("title")}
        sx={{
          pointerEvents: "auto",
          position: "relative",
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          maxWidth: "100%",
          pl: 1.25,
          pr: caption ? 2.5 : 1.25,
          py: 1.25,
          borderRadius: "var(--radius-full)",
          border: "1px solid var(--border)",
          /*
            `SelectionReply`'s floating pill, to the token: `--popover` on a
            `--border` hairline with a literal shadow, because the palette's
            own `--shadow-*` are switched off (`--shadow-opacity: 0`) and a
            surface that hovers over the page has to say so somehow. Solid, not
            translucent -- it is small enough that opacity would buy nothing but
            an unreadable caption over whatever it happens to sit on.
          */
          backgroundColor: "var(--popover)",
          boxShadow: "0 4px 16px rgb(0 0 0 / 0.18)",
          animation: "tf26-voice-dock-in 200ms cubic-bezier(0.16, 1, 0.3, 1)",
          "@keyframes tf26-voice-dock-in": {
            from: { opacity: 0, transform: "translateY(1.25rem)" },
            to: { opacity: 1, transform: "none" },
          },
          // The close button is an affordance, not furniture: it appears when
          // the pointer is on the dock, and when a keyboard reaches it.
          "&:hover [data-voice-close], &:focus-within [data-voice-close]": {
            opacity: 1,
          },
          "@media (prefers-reduced-motion: reduce)": { animation: "none" },
        }}
      >
        <VuiBox
          sx={{ width: ORB_SIZE, height: ORB_SIZE, flexShrink: 0, position: "relative" }}
        >
          <VoicePoweredOrb
            // Never its own microphone: the recorder already holds the only one.
            enableVoiceControl={false}
            level={voice.level}
            hue={PHASE_HUE[phase] ?? 0}
            maxHoverIntensity={0.8}
          />
        </VuiBox>

        {caption ? (
          <VuiTypography
            variant="caption"
            sx={{
              // `--control-ink`, not `--text-faint`: faint is for decoration,
              // and this is the one line of the dock that has to be read.
              color: failure ? "var(--destructive)" : "var(--control-ink)",
              lineHeight: 1.35,
            }}
          >
            {caption}
          </VuiTypography>
        ) : null}

        <VuiBox
          component="button"
          type="button"
          data-voice-close=""
          onClick={cancel}
          aria-label={t("close")}
          title={t("close")}
          display="flex"
          alignItems="center"
          justifyContent="center"
          sx={{
            position: "absolute",
            top: -8,
            right: -8,
            width: 24,
            height: 24,
            padding: 0,
            cursor: "pointer",
            borderRadius: "var(--radius-full)",
            border: "1px solid var(--border)",
            backgroundColor: "var(--popover)",
            color: "var(--text-faint)",
            opacity: 0,
            transition: "opacity 150ms ease, color 150ms ease",
            "&:hover": { color: "var(--foreground)" },
            "&:focus-visible": {
              opacity: 1,
              outline: "2px solid var(--ring)",
              outlineOffset: 2,
            },
            "@media (prefers-reduced-motion: reduce)": { transition: "none" },
          }}
        >
          <X size={13} />
        </VuiBox>

        {/* The phase, for anyone who is not watching the colour change. */}
        <VuiBox
          aria-live="polite"
          sx={{
            position: "absolute",
            width: 1,
            height: 1,
            overflow: "hidden",
            clip: "rect(0 0 0 0)",
            whiteSpace: "nowrap",
          }}
        >
          {failure ? t(failure) : t(phase)}
        </VuiBox>
      </VuiBox>
    </VuiBox>
  );
}
