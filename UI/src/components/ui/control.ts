import type { Theme } from "@mui/material/styles";

/**
 * The shape every interactive control on a form shares.
 *
 * Controls were drifting because nothing said what a control looks like: the
 * dropdown stood 44px tall with a 15px radius, `VuiInput` 31px with the same
 * 15px, and the buttons 41px and 35px with 12px. Four heights and two radii on
 * one row.
 *
 * The inputs read as *pills* and the dropdown as a rounded rectangle **from the
 * same radius token** — 15px on a 31px box is half its height, so the corners
 * meet and the sides disappear. Radius alone was never the fix; height and
 * radius have to be decided together, which is why they live here as one pair
 * rather than as two tokens anyone can set independently.
 *
 * `lg` (15px) at `HEIGHT` (44px) is the app's rounded rectangle, and 44px is a
 * comfortable pointer target — the dropdown was already this size, so the other
 * controls come up to meet it rather than it shrinking to meet them.
 */

/** Every control on a form row is exactly this tall, so they line up. */
export const CONTROL_HEIGHT = 44;

type WithBorders = Theme & {
  borders: { borderRadius: Record<string, string>; borderWidth: Record<number, string> };
  typography: Theme["typography"] & { size: Record<string, string> };
};

/**
 * Height, radius and type size for a control.
 *
 * Spread into a control's `sx`. Deliberately does not set colour or border:
 * those already come from the theme's own input tokens and flip correctly with
 * the mode, and restating them here would be a second place to keep in step.
 */
export function controlShape(theme: Theme) {
  const t = theme as WithBorders;
  return {
    height: `${CONTROL_HEIGHT}px`,
    minHeight: `${CONTROL_HEIGHT}px`,
    borderRadius: t.borders.borderRadius.lg,
    fontSize: t.typography.size.sm,
    // 1.6 rather than the browser default: Turkish descenders ("ğ", "ş") were
    // being clipped at the default line height.
    lineHeight: 1.6,
  } as const;
}

/** Horizontal padding for a control with no icon or caret in it. */
export const CONTROL_PADDING_X = "14px";
