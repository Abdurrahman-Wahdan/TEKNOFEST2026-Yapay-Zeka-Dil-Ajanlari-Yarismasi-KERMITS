"use client";

import { VuiBox } from "@/components/vision";

/**
 * The app's switch.
 *
 * Drawn rather than imported. MUI's `Switch` brings its own palette, and on a
 * Vision surface it renders as a pale grey capsule with a white knob — the one
 * control on the page not using our tokens, and the reason this file exists:
 * `AdvancedMenu` had already drawn its own for the composer, and the automations
 * board then reached for MUI's, so the app had two switches that looked nothing
 * alike. Now it has one.
 *
 * Two exports, one paint:
 *
 *   - `SwitchTrack` is the visual alone, for a control that is already a button
 *     — `AdvancedMenu`'s rows are a whole labelled row you can click, and
 *     nesting a button inside that button would be invalid markup.
 *   - `Toggle` is the standalone control, for a switch sitting on its own with
 *     nothing but a tooltip beside it — the automations board's enable/pause.
 */

/** The capsule and its knob. `aria-hidden`: the control around it carries state. */
export function SwitchTrack({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden
      style={{
        display: "flex",
        alignItems: "center",
        flexShrink: 0,
        width: 34,
        height: 20,
        padding: 2,
        borderRadius: "var(--radius-full)",
        backgroundColor: on ? "var(--primary)" : "var(--muted)",
        transition: "background-color 150ms ease",
      }}
    >
      <span
        style={{
          width: 16,
          height: 16,
          borderRadius: "var(--radius-full)",
          backgroundColor: on ? "var(--primary-foreground)" : "var(--control-ink)",
          transform: on ? "translateX(14px)" : "translateX(0)",
          transition: "transform 150ms ease, background-color 150ms ease",
        }}
      />
    </span>
  );
}

/**
 * A switch on its own.
 *
 * `role="switch"` with `aria-checked` rather than a checkbox: it turns something
 * on and off right now, it does not stage a value for a form submit. The 34px
 * track is smaller than a comfortable pointer target, so the button pads out to
 * 36px around it and the padding is part of the hit area.
 */
export function Toggle({
  on,
  onChange,
  label,
  disabled = false,
}: {
  on: boolean;
  onChange: (on: boolean) => void;
  /** The accessible name — what the switch controls, not its current state. */
  label: string;
  disabled?: boolean;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      // The same string as the hover hint. `RoundButton` beside it does this
      // too, which is what let the automations row drop its MUI `Tooltip`
      // wrappers -- those forced each control into an extra `<span>` and were
      // the reason the three sat on three different centres.
      title={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      display="flex"
      alignItems="center"
      justifyContent="center"
      sx={{
        width: 36,
        height: 36,
        flexShrink: 0,
        padding: 0,
        border: "none",
        backgroundColor: "transparent",
        borderRadius: "var(--radius-full)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "background-color 150ms ease",
        "&:hover:not(:disabled)": { backgroundColor: "var(--muted)" },
        "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
      }}
    >
      <SwitchTrack on={on} />
    </VuiBox>
  );
}
