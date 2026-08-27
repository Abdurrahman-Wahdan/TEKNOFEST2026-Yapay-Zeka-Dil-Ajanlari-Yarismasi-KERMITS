"use client";

import { Download } from "lucide-react";

import { VuiBox } from "@/components/vision";

/**
 * The "save this as a file" control.
 *
 * A sibling of `chat/AttachButton`, not a copy of it with a different icon:
 * that one means "hand this to the assistant" and lives in the assistant's own
 * folder, this one means "take this away with you" and belongs to the table.
 * They share their measurements deliberately — 26px target, 15px glyph,
 * `--control-ink` at rest — because they sit next to each other in the same
 * header cell, and two quiet controls of two different sizes in one corner is
 * the thing that reads as a mistake.
 *
 * Always visible. It appears once per table rather than once per row, so there
 * is no `tr:hover` for a reveal to hang off; a hidden one would be a button
 * nobody could press.
 */
export function ExportButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      display="inline-flex"
      alignItems="center"
      justifyContent="center"
      sx={{
        width: 26,
        height: 26,
        border: "none",
        padding: 0,
        cursor: "pointer",
        borderRadius: "var(--radius-full)",
        backgroundColor: "transparent",
        color: "var(--control-ink)",
        transition: "background-color 150ms ease, color 150ms ease",
        "&:hover": { backgroundColor: "var(--muted)", color: "var(--foreground)" },
        "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
      }}
    >
      <Download size={15} aria-hidden="true" />
    </VuiBox>
  );
}
