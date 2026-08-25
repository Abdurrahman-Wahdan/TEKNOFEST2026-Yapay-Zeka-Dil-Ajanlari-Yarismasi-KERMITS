"use client";

import type { Theme } from "@mui/material/styles";

import { VuiBox, VuiTypography } from "@/components/vision";
import { CONTROL_PADDING_X, controlShape } from "./control.ts";

/**
 * The template's theme, with the keys it adds on top of MUI's.
 *
 * `inputColors` is the set the template's own inputs are built from, so a
 * control that reads it stays in step with every other field in the app —
 * including when the mode flips.
 */
type VisionTheme = Theme & {
  palette: Theme["palette"] & {
    white: { main: string };
    text: { main: string };
    inputColors: {
      backgroundColor: string;
      borderColor: { main: string; focus: string };
    };
  };
};

/**
 * The app's dropdown.
 *
 * One component rather than a `Select` configured differently at each call
 * site, because the things that go wrong with a select are the same every time.
 *
 * Built on a native `<select>` on purpose. MUI's `Select` renders an inner div
 * sized to its text, so clicking the empty half of a full-width control does
 * nothing — the complaint that started this — and the Vision theme's own
 * `MuiOutlinedInput` overrides outrank `sx`, so correcting it from the call
 * site does not stick. A native select is clickable across its whole box by
 * definition, shows its value without clipping, and gets keyboard and mobile
 * behaviour for free.
 *
 * Styling goes through `VuiBox`'s `sx`, which lands on the element itself
 * rather than on a descendant, so it is not competing with the theme. Colours
 * are read from the theme rather than written as literals.
 */
export interface DropdownOption {
  value: string;
  label: string;
  /** Native select group, used to keep semantically different products apart. */
  group?: string;
  /** Greyed out and unselectable — a choice that exists but is not available. */
  disabled?: boolean;
}

export function Dropdown({
  label,
  value,
  options,
  onChange,
  minWidth = "14rem",
  fullWidth = true,
  disabled = false,
}: {
  label?: string;
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  minWidth?: string;
  fullWidth?: boolean;
  /**
   * The whole control, not one option — a choice that cannot be made *yet*
   * because it depends on a control above it (the automations composer's
   * minutes, which mean nothing until an hour is set). `DropdownOption.disabled`
   * greys out a single row inside an otherwise usable select; this greys out the
   * select.
   */
  disabled?: boolean;
}) {
  return (
    <VuiBox sx={{ minWidth, width: fullWidth ? "100%" : undefined }}>
      {label && (
        <VuiBox mb={0.75}>
          <VuiTypography variant="caption" color="text">
            {label}
          </VuiTypography>
        </VuiBox>
      )}

      <VuiBox
        component="select"
        value={value}
        disabled={disabled}
        // The visible label is a caption box above the control, not a `<label
        // for>`, so nothing could associate the two: a screen reader announced
        // "combobox" with no name, and the assistant's page snapshot -- whose most
        // useful section is "what is currently selected" -- came back empty.
        aria-label={label}
        onChange={(e: { target: { value: string } }) => onChange(e.target.value)}
        sx={(theme: VisionTheme) => ({
          ...controlShape(theme),
          width: "100%",
          // Room for the caret drawn below, so a long value never runs under it.
          padding: `0 40px 0 ${CONTROL_PADDING_X}`,
          border: `${theme.borders.borderWidth[1]} solid`,
          // Every colour comes from the theme's own input tokens, which already
          // flip with the mode. Written as literal rgba(255,255,255,…) they
          // looked right in dark and vanished in light.
          borderColor: theme.palette.inputColors.borderColor.main,
          background: theme.palette.inputColors.backgroundColor,
          color: theme.palette.white.main,
          fontFamily: "inherit",
          cursor: disabled ? "not-allowed" : "pointer",
          appearance: "none",
          "&:disabled": { opacity: 0.5 },
          // The caret, drawn rather than imported, so there is no icon font or
          // asset to keep in step with the palette.
          backgroundImage: [
            `linear-gradient(45deg, transparent 50%, ${theme.palette.text.main} 50%)`,
            `linear-gradient(135deg, ${theme.palette.text.main} 50%, transparent 50%)`,
          ].join(", "),
          backgroundPosition: "calc(100% - 20px) 50%, calc(100% - 14px) 50%",
          backgroundSize: "6px 6px, 6px 6px",
          backgroundRepeat: "no-repeat",
          "&:hover:not(:disabled)": {
            borderColor: theme.palette.inputColors.borderColor.focus,
          },
          "&:focus-visible": {
            outline: "none",
            borderColor: theme.palette.inputColors.borderColor.focus,
          },
          // The popup is drawn by the OS and cannot inherit a translucent
          // background, so these two need solid values.
          "& option": {
            background: theme.palette.background.default,
            color: theme.palette.white.main,
          },
        })}
      >
        {Object.entries(
          options.reduce<Record<string, DropdownOption[]>>((groups, option) => {
            const key = option.group ?? "";
            (groups[key] ??= []).push(option);
            return groups;
          }, {}),
        ).map(([group, grouped]) =>
          group ? (
            <optgroup key={group} label={group}>
              {grouped.map((option) => (
                <option key={option.value} value={option.value} disabled={option.disabled}>
                  {option.label}
                </option>
              ))}
            </optgroup>
          ) : grouped.map((option) => (
            <option key={option.value} value={option.value} disabled={option.disabled}>
              {option.label}
            </option>
          )),
        )}
      </VuiBox>
    </VuiBox>
  );
}
