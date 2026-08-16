"use client";

import { alpha, type Theme } from "@mui/material/styles";
import type { ReactNode } from "react";

import { VuiTypography } from "@/components/vision";

/**
 * A small rounded label — a badge cell, a capability tag, a status chip.
 *
 * One component rather than the same handful of props repeated wherever a pill
 * is needed, because the thing that goes wrong is always the same: the text
 * inside sits high or low in the capsule. `inline-block` with vertical padding
 * leaves the glyph's position at the mercy of the font's line-height, and the
 * caption variant's line-height is not 1. So this centres properly instead:
 * `inline-flex` with `alignItems: center`, a fixed height, and `lineHeight: 1`
 * on the text so the box is exactly the text's box.
 *
 * Colours are derived from the theme's own palette rather than written as
 * literals: a fixed `rgba(255,255,255,…)` fill reads as a tint in dark mode and
 * as nothing at all in light.
 */
export type PillTone = "neutral" | "ok" | "warn" | "bad";

type ToneColours = { fg: string; bg: string };

function tone(theme: Theme, name: PillTone): ToneColours {
  const base = {
    neutral: (theme.palette as unknown as { text: { main: string } }).text.main,
    ok: theme.palette.success.main,
    warn: theme.palette.warning.main,
    // A bound the current inputs actually violate, as opposed to `warn`, which
    // is a bound merely being stated.
    bad: theme.palette.error.main,
  }[name];

  return { fg: base, bg: alpha(base, 0.14) };
}

/** The pill's height, and its line height -- the two must stay equal. */
const PILL_HEIGHT = 22;

export function Pill({
  children,
  tone: name = "neutral",
}: {
  children: ReactNode;
  tone?: PillTone;
}) {
  // `line-height` equal to the height, on an inline-block. Not flexbox.
  //
  // Two attempts centred the *box* and left the text low. Flex alignment
  // positions the line box, and the glyphs sit inside it wherever the font's
  // ascent and descent put them -- asymmetric in every real typeface, so a
  // word with a descender ("gram") hangs lower than one without ("birim").
  //
  // Setting the line height to the pill's own height makes the browser split
  // the leftover space evenly above and below the text as half-leading, which
  // is the one technique that centres single-line text by construction rather
  // than by luck of the metrics.
  return (
    <VuiTypography
      component="span"
      variant="caption"
      fontWeight="medium"
      sx={(theme: Theme) => ({
        color: tone(theme, name).fg,
        background: tone(theme, name).bg,
        display: "inline-block",
        height: PILL_HEIGHT,
        lineHeight: `${PILL_HEIGHT}px`,
        paddingInline: theme.spacing(1.5),
        borderRadius: theme.shape.borderRadius * 3,
        whiteSpace: "nowrap",
        // The pill sits in a table cell whose own line box would otherwise
        // drag it off the row's baseline.
        verticalAlign: "middle",
      })}
    >
      {children}
    </VuiTypography>
  );
}
