"use client";

import type { ReactNode } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";

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
 */
export type PillTone = "neutral" | "ok" | "warn";

const TONES: Record<PillTone, { color: string; background: string }> = {
  neutral: { color: "text.main", background: "rgba(255, 255, 255, 0.08)" },
  ok: { color: "success.main", background: "rgba(1, 181, 116, 0.14)" },
  warn: { color: "warning.main", background: "rgba(255, 181, 71, 0.16)" },
};

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: PillTone;
}) {
  const { color, background } = TONES[tone];

  return (
    <VuiBox
      component="span"
      px={1.5}
      borderRadius="lg"
      sx={{
        background,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        height: "22px",
        whiteSpace: "nowrap",
      }}
    >
      <VuiTypography
        variant="caption"
        fontWeight="medium"
        sx={{ color, lineHeight: 1 }}
      >
        {children}
      </VuiTypography>
    </VuiBox>
  );
}
