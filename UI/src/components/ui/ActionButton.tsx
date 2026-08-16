"use client";

import type { Theme } from "@mui/material/styles";
import type { MouseEvent, ReactNode } from "react";

import { VuiButton } from "@/components/vision";
import { controlShape } from "./control.ts";

/**
 * A button that sits on a form row beside fields.
 *
 * `VuiButton` on its own comes out 41px tall with a 12px radius while the
 * fields beside it are 44px with 15px, so the row reads as two different
 * design languages meeting in the middle. This is the same button with the
 * shared control geometry applied, for the one place it matters: a control
 * standing in a line with other controls.
 *
 * Buttons that are not on a form row (a toolbar, a card action) keep using
 * `VuiButton` directly — they are not lining up with anything, so forcing a
 * field's height on them would make them look oversized instead.
 */
export function ActionButton({
  children,
  onClick,
  disabled = false,
  color = "info",
  variant = "contained",
}: {
  children: ReactNode;
  onClick?: (event: MouseEvent<HTMLElement>) => void;
  disabled?: boolean;
  color?: string;
  variant?: "contained" | "outlined" | "text";
}) {
  return (
    <VuiButton
      color={color}
      variant={variant}
      onClick={onClick}
      disabled={disabled}
      sx={(theme: Theme) => ({
        ...controlShape(theme),
        // The template sets its own radius on MuiButton, which outranks a
        // plain value here the same way its input overrides do.
        borderRadius: `${controlShape(theme).borderRadius} !important`,
        paddingX: "24px",
        whiteSpace: "nowrap",
      })}
    >
      {children}
    </VuiButton>
  );
}
