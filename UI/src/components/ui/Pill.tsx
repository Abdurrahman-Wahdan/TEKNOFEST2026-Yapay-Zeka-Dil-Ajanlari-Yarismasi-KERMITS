"use client";

import { alpha, type Theme } from "@mui/material/styles";
import type { ReactNode } from "react";

import { VuiTypography } from "@/components/vision";

/**
 * A small status label — a badge cell, a capability tag, a state chip.
 *
 * Drawn the way Vision UI's own status badges are, the ones on the template's
 * Authors table: a rounded rectangle at `borderRadius.md` rather than a full
 * capsule. Adopting that shape here rather than at the call sites is the point
 * — this is the one pill in the app, so the four widgets that use it inherit
 * the look without any of them being touched.
 *
 * Always outlined, never filled. The tone lives in the **border**, and the text
 * stays ink in every variant. A filled chip has to solve its own contrast
 * problem — ink follows the mode, so near-white landed on a light amber fill at
 * about 1.9:1 — and it shouts louder than a status note needs to. An outline
 * says the same thing at the same size without either problem, and it means
 * `neutral` and the tones are one family rather than two looks.
 *
 * Colours come from the theme rather than as literals: a fixed
 * `rgba(255,255,255,…)` fill reads as a tint in dark mode and as nothing at all
 * in light.
 */
export type PillTone = "neutral" | "ok" | "warn" | "bad";

type ToneColours = { fg: string; bg: string; border: string };

/**
 * `neutral` is the outlined one: no fill, an ink hairline. Every other tone is
 * making a claim about state, so it earns a fill.
 */
function tone(theme: Theme, name: PillTone): ToneColours {
  const palette = theme.palette as unknown as {
    text: { main: string };
    white: { main: string };
  };

  if (name === "neutral") {
    // Quieter than the tones on purpose: it is a label, not a claim. Muted ink
    // for both the text and the hairline.
    return { fg: palette.text.main, bg: "transparent", border: alpha(palette.text.main, 0.55) };
  }

  const accent = {
    ok: theme.palette.success.main,
    warn: theme.palette.warning.main,
    // A bound the current inputs actually violate, as opposed to `warn`, which
    // is a bound merely being stated.
    bad: theme.palette.error.main,
  }[name];

  // `white` is the template's name for *ink* -- it follows the mode. The text
  // sits on the card, not on a fill, so that is exactly what it should do.
  return { fg: palette.white.main, bg: "transparent", border: accent };
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
      sx={(theme: Theme) => {
        const { fg, bg, border } = tone(theme, name);
        return {
          color: fg,
          background: bg,
          // 1px of the box is border, so the inner height is 2px short of
          // PILL_HEIGHT and the line height has to match that, not the box.
          border: `1px solid ${border}`,
          display: "inline-block",
          height: PILL_HEIGHT,
          lineHeight: `${PILL_HEIGHT - 2}px`,
          paddingInline: theme.spacing(1.25),
          // `md`, the radius the template's own status badges use. A capsule
          // reads as a tag; this reads as a state.
          borderRadius: theme.spacing(1),
          whiteSpace: "nowrap",
          // The pill sits in a table cell whose own line box would otherwise
          // drag it off the row's baseline.
          verticalAlign: "middle",
        };
      }}
    >
      {children}
    </VuiTypography>
  );
}
