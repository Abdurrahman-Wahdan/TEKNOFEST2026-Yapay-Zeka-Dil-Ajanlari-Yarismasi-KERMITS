"use client";

import type { Theme } from "@mui/material/styles";
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent, type ReactNode } from "react";
import { IoClose, IoSearch } from "react-icons/io5";

import { VuiBox } from "@/components/vision";
import { controlShape, CONTROL_PADDING_X } from "./control.ts";

type VisionTheme = Theme & {
  borders: { borderRadius: Record<string, string>; borderWidth: Record<number, string> };
  palette: Theme["palette"] & {
    inputColors: { borderColor: { main: string; focus: string }; backgroundColor: string };
    white: { main: string };
  };
  typography: Theme["typography"] & { size: Record<string, string> };
};

/**
 * A search icon that turns into a field when you press it.
 *
 * A card header has room for one glyph and not for a permanently open search
 * bar, and a list that is usually browsed rather than searched should not
 * spend a full row on a box most visits never touch. Pressing the glyph is the
 * whole affordance; the field it opens is the app's standard control geometry,
 * so it lines up with anything else on the row.
 *
 * Controlled: the query lives with whoever is filtering by it, so this holds
 * nothing but whether it is open. Collapsing always clears — a filter that is
 * still applied behind a closed box is a list that looks broken, and the count
 * beside it would be the only clue.
 *
 * Nothing here knows what is being searched, so the next list that needs one
 * passes its own `value`/`onChange` and its own labels.
 */
export function SearchField({
  value,
  onChange,
  label,
  placeholder,
  clearLabel,
  width = "16rem",
}: {
  value: string;
  onChange: (value: string) => void;
  /** Names the control for the button's tooltip and for assistive tech. */
  label: string;
  placeholder?: string;
  clearLabel?: string;
  /** How wide the open field is. Falls back to full width when the row wraps. */
  width?: string;
}) {
  const [open, setOpen] = useState(value !== "");
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Focus follows the reveal: opening a field and leaving the caret elsewhere
  // makes the user click the thing they just asked for.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const close = () => {
    onChange("");
    setOpen(false);
  };

  if (!open) {
    return (
      <IconButton label={label} onClick={() => setOpen(true)}>
        <IoSearch size="17px" />
      </IconButton>
    );
  }

  return (
    <VuiBox
      display="flex"
      alignItems="center"
      sx={(theme: VisionTheme) => ({
        ...controlShape(theme),
        width,
        maxWidth: "100%",
        paddingLeft: CONTROL_PADDING_X,
        paddingRight: "6px",
        gap: "8px",
        border: `${theme.borders.borderWidth[1]} solid`,
        borderColor: theme.palette.inputColors.borderColor.main,
        background: theme.palette.inputColors.backgroundColor,
        color: theme.palette.white.main,
        "&:hover": { borderColor: theme.palette.inputColors.borderColor.focus },
        "&:focus-within": { borderColor: theme.palette.inputColors.borderColor.focus },
      })}
    >
      <VuiBox display="flex" color="text" flexShrink={0} aria-hidden="true">
        <IoSearch size="16px" />
      </VuiBox>
      <VuiBox
        component="input"
        ref={inputRef}
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={label}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        // Escape is what a keyboard user reaches for to undo a search, and
        // blurring an empty box means they opened it and changed their mind.
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === "Escape") close();
        }}
        onBlur={() => {
          if (value === "") setOpen(false);
        }}
        sx={(theme: VisionTheme) => ({
          flex: 1,
          minWidth: 0,
          height: "100%",
          border: "none",
          outline: "none",
          background: "transparent",
          color: theme.palette.white.main,
          fontFamily: "inherit",
          fontSize: theme.typography.size.sm,
          // Safari draws its own cancel button on type="search", which would
          // sit beside the one below saying the same thing.
          "&::-webkit-search-cancel-button": { display: "none" },
        })}
      />
      <IconButton label={clearLabel ?? label} onClick={close}>
        <IoClose size="16px" />
      </IconButton>
    </VuiBox>
  );
}

/** The quiet round control both states use — same treatment as the table's
    own row buttons, so a card header never turns into a toolbar. */
function IconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
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
      flexShrink={0}
      sx={{
        width: 32,
        height: 32,
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
      {children}
    </VuiBox>
  );
}
