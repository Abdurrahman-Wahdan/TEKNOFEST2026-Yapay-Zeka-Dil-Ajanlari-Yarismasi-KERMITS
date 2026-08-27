"use client";

import { Sparkles } from "lucide-react";

import { VuiBox } from "@/components/vision";

/**
 * The "hand this to the assistant" control.
 *
 * Revealed on hover of its row, and always present for the keyboard -- an
 * `opacity: 0` button is still focusable, so hiding it without a `:focus-visible`
 * escape is a tab trap. Same pattern the attachment tray's remove button uses.
 *
 * One copy, in the assistant's own folder rather than inside the table it started
 * in: the same press means the same thing on a table row, on a whole table and on
 * a report, and a second lookalike is how one of them ends up a different size or
 * a different icon from its neighbours.
 */
export function AttachButton({
  label,
  onClick,
  alwaysVisible,
}: {
  label: string;
  onClick: () => void;
  /**
   * Skip the hover reveal.
   *
   * Set wherever there is only ever one of these -- a table's own control, a
   * report's -- because `tr:hover` is the reveal and nothing outside a table row
   * would ever match it, so a hidden button there is a button nobody can press.
   */
  alwaysVisible?: boolean;
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
        // The same grey every other quiet control in the app uses. Not
        // `--text-faint`, which is 2.49:1 and for decoration only.
        color: "var(--control-ink)",
        opacity: alwaysVisible ? 1 : 0,
        transition: "opacity 150ms ease, background-color 150ms ease, color 150ms ease",
        "tr:hover &, &:focus-visible": { opacity: 1 },
        "&:hover": { backgroundColor: "var(--muted)", color: "var(--foreground)" },
        "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
      }}
    >
      <Sparkles size={15} aria-hidden="true" />
    </VuiBox>
  );
}
