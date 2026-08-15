"use client";

import type { Theme } from "@mui/material/styles";

import { VuiBox, VuiTypography } from "@/components/vision";
import { CONTROL_PADDING_X, controlShape } from "./control.ts";

type VisionTheme = Theme & {
  palette: Theme["palette"] & {
    white: { main: string };
    inputColors: {
      backgroundColor: string;
      borderColor: { main: string; focus: string };
    };
  };
};

/**
 * The app's text field, built to match `Dropdown` exactly.
 *
 * Not `VuiInput`: that renders a styled `InputBase`, so the shape lives on a
 * wrapper div and the real `<input>` inside it is 22px tall with no radius at
 * all. Setting a height from the call site changed the wrapper and not the
 * field, which is how an amount box ended up 31px beside a 44px dropdown.
 *
 * A bare `<input>` puts the border, the background and the height on the one
 * element that the user actually sees and clicks, so `controlShape` lands
 * where it is meant to. Colours come from the same `inputColors` tokens the
 * template's own fields use, so this follows the mode without a second palette.
 */
export function TextField({
  label,
  value,
  onChange,
  type = "text",
  min,
  transform,
  disabled = false,
  minWidth = "10rem",
  fullWidth = true,
}: {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "number";
  min?: number;
  /** Applied before `onChange`, e.g. upper-casing a currency code. */
  transform?: (raw: string) => string;
  disabled?: boolean;
  /** Only when `fullWidth` is false: the width the field starts from. */
  minWidth?: string;
  /**
   * Full width by default. Set false on a flex row, where the field shares the
   * line with its neighbours and grows to fill it, rather than claiming the
   * whole row and pushing them onto the next one.
   */
  fullWidth?: boolean;
}) {
  return (
    <VuiBox
      sx={fullWidth ? { width: "100%" } : { flex: `1 1 ${minWidth}`, minWidth }}
    >
      {label && (
        <VuiBox mb={0.75}>
          <VuiTypography variant="caption" color="text">
            {label}
          </VuiTypography>
        </VuiBox>
      )}

      <VuiBox
        component="input"
        type={type}
        inputMode={type === "number" ? "numeric" : undefined}
        value={value}
        min={min}
        disabled={disabled}
        onChange={(e: { target: { value: string } }) =>
          onChange(transform ? transform(e.target.value) : e.target.value)
        }
        sx={(theme: VisionTheme) => ({
          ...controlShape(theme),
          width: "100%",
          padding: `0 ${CONTROL_PADDING_X}`,
          border: `${theme.borders.borderWidth[1]} solid`,
          borderColor: theme.palette.inputColors.borderColor.main,
          background: theme.palette.inputColors.backgroundColor,
          color: theme.palette.white.main,
          fontFamily: "inherit",
          "&:hover": { borderColor: theme.palette.inputColors.borderColor.focus },
          "&:focus-visible": {
            outline: "none",
            borderColor: theme.palette.inputColors.borderColor.focus,
          },
          "&:disabled": { opacity: 0.5, cursor: "not-allowed" },
          // The spinners are a second, differently shaped control sitting
          // inside this one, and they clip against the rounded corner.
          "&::-webkit-outer-spin-button, &::-webkit-inner-spin-button": {
            WebkitAppearance: "none",
            margin: 0,
          },
          MozAppearance: "textfield",
        })}
      />
    </VuiBox>
  );
}

/** A numeric field. The spinner-free, correctly shaped `<input type="number">`. */
export function NumberField(
  props: Omit<Parameters<typeof TextField>[0], "type" | "transform">,
) {
  return <TextField {...props} type="number" />;
}
