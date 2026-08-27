"use client";

import type { ReactNode } from "react";

import { VuiBox } from "@/components/vision";

/**
 * A chip that is on or off.
 *
 * The paint rule is the composer's, which `ChatComposer`'s Advanced chip
 * documents at length and arrived at by measurement: tint the surface with a
 * real `color-mix` of the brand over the card and match the text to
 * `--primary-strong`, never `--info-subtle` (which in the dark palette is
 * `--accent`, all but black, so an active chip looked inactive) and never
 * `--primary` as text (2.4:1 over its own 22% tint in light mode).
 *
 * Off is transparent **but outlined**, which is the one place this departs from
 * the composer's "borderless until active" rule — deliberately, and because a
 * *set* is not a toggle. The composer's chips are one or two controls in a row
 * of obvious buttons, so a bare label still reads as pressable. Seven of these
 * start off together with nothing else in the row, and borderless they render
 * as seven words: the automations composer shipped that way and the day picker
 * looked like a caption. The hairline is `--border`, the same one the card and
 * the rows use, so it says "control" without competing with the fill that says
 * "chosen".
 */
export function ToggleChip({
  label,
  on,
  onClick,
  disabled = false,
}: {
  label: ReactNode;
  on: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      // `aria-pressed`, not `role="switch"`: these are a set of choices being
      // selected, not one thing being turned on and off.
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      display="flex"
      alignItems="center"
      justifyContent="center"
      px={1.5}
      sx={{
        height: 36,
        minWidth: 44,
        flexShrink: 0,
        borderStyle: "solid",
        borderWidth: "1px",
        borderRadius: "var(--radius-full)",
        fontFamily: "inherit",
        fontSize: "0.875rem",
        fontWeight: "var(--weight-medium)",
        whiteSpace: "nowrap",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "background-color 150ms ease, color 150ms ease",
        ...(on
          ? {
              backgroundColor:
                "color-mix(in srgb, var(--primary) 22%, var(--card))",
              color: "var(--primary-strong)",
              // The tint already draws the shape. A `--border` hairline over it
              // would outline the chosen day more faintly than the unchosen
              // ones beside it, so the border becomes the tint's own edge.
              borderColor: "color-mix(in srgb, var(--primary) 40%, var(--card))",
            }
          : {
              backgroundColor: "transparent",
              borderColor: "var(--border)",
              // Off is the chip's normal state and its label is the only thing
              // saying what it does, so it is a control label rather than
              // decoration -- `--control-ink`, not `--text-faint`.
              color: "var(--control-ink)",
              "&:hover:not(:disabled)": {
                backgroundColor: "var(--muted)",
                color: "var(--foreground)",
                borderColor: "var(--control-ink)",
              },
            }),
        "&:focus-visible": {
          outline: "2px solid var(--ring)",
          outlineOffset: 2,
        },
      }}
    >
      {label}
    </VuiBox>
  );
}
