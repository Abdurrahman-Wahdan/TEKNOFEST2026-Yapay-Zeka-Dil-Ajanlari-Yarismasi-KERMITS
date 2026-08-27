"use client";

import type { MouseEvent, ReactNode } from "react";

import { VuiBox } from "@/components/vision";

/**
 * The app's round icon button — a glyph with a hit target and no chrome at rest.
 *
 * Lifted out of `ChatComposer`, which had it private, when the automations
 * composer needed the same mic button. That one had reached for MUI's
 * `IconButton` instead, which brings its own ripple, its own hover and its own
 * sizing, and came out as the one control on the profile page not using our
 * tokens: an 18px glyph at `--control-ink` with no visible target, next to a
 * 36px one everywhere else. A second copy of the rule would have drifted the
 * same way again, so there is one button and both surfaces import it.
 *
 * Two states, because the composer row has exactly two kinds of button:
 * `filled` is the primary action (send, stop, record-as-primary) and carries the
 * brand fill; everything else is a bare glyph that earns a `--muted` disc on
 * hover. Colours are tokens, so both follow the mode.
 */
export function RoundButton({
  label,
  onClick,
  children,
  disabled,
  filled,
  ml = 0,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  disabled?: boolean;
  /** The send/stop button: solid, so it reads as the primary action. */
  filled?: boolean;
  /** Distance from the control on its left, in px. See OPTICAL_GAP_PX. */
  ml?: number;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={(event: MouseEvent) => {
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
        ml: `${ml}px`,
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
              // No dimming when disabled. In the chat row these glyphs have to
              // read as one set in one shade, and fading one to 0.5 made it a
              // visibly lighter grey than the others. Disabled is carried by the
              // semantics instead: not focusable, not clickable, announced as
              // unavailable.
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
