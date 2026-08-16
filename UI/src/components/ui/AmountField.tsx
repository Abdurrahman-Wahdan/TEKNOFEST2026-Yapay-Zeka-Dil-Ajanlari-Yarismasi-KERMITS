"use client";

import type { Theme } from "@mui/material/styles";
import { useLocale } from "next-intl";
import { useRef, useState, type ChangeEvent } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";
import {
  countSignificant,
  localeSeparators,
  padDecimals,
  positionAfterSignificant,
  toCanonical,
  toDisplay,
} from "@/lib/amount-input";
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
 * A number field that groups thousands as you type, in whichever direction
 * this locale actually groups them.
 *
 * Not `<input type="number">`: that control cannot show "1.000.000" or
 * "1,000,000" at all -- there is no grouped display for a native number
 * input in any browser -- and past six digits a loan amount stops being a
 * figure you can read at a glance and becomes a row of blurred zeros. This
 * renders as text, sanitising every keystroke down to a canonical
 * `"1234.5"`-shaped string (via `amount-input.ts`) and re-displaying it
 * grouped, so the value the rest of the app sees (`Number(value)`,
 * `params.amount`) never changes shape.
 *
 * Decimals are opt-in (`allowDecimal`): a loan or a gram of gold wants a
 * fraction, a term in months never does. One component either way -- money
 * and grams format identically, and a term goes through `IntegerField`,
 * which is this same machinery with `allowDecimal` off and no separate
 * codepath to drift from it.
 *
 * Reformatting on every keystroke changes the string length -- adding a
 * grouping separator moves everything after it one character to the right --
 * so the browser's default caret placement (end of value) fires on every
 * digit typed in the middle of a number. The caret is walked instead:
 * counted in *significant* characters (digits and the decimal point, not
 * separators) before the edit, then placed after the same count once the new
 * grouped string exists.
 *
 * A whole number shows no room for a fraction unless something is already
 * there to see -- "1,000,000" reads as an integer, not as an amount that
 * happens to have no kuruş. Padded to `MIN_DECIMALS` while the field is not
 * focused, so a fresh page shows "1,000,000.00" and clicking in to edit shows
 * exactly what is stored, not a padded value fighting every keystroke. The
 * padding is display-only -- `value`/`onChange` are never touched by it, so
 * clicking into and back out of an untouched field does not itself trigger a
 * change and re-run whatever the parent wires to `onChange`.
 */
const MIN_DECIMALS = 2;
export function AmountField({
  label,
  value,
  onChange,
  allowDecimal = true,
  disabled = false,
  minWidth = "10rem",
  fullWidth = true,
  placeholder = "0",
}: {
  label?: string;
  /** Canonical: ASCII digits, "." decimal, no grouping -- e.g. "1000000.5". */
  value: string;
  /** Receives the same canonical shape back. */
  onChange: (value: string) => void;
  allowDecimal?: boolean;
  disabled?: boolean;
  minWidth?: string;
  fullWidth?: boolean;
  /** Shown only while `value` is empty -- a field starts blank, not carrying
      a real figure nobody typed, and this is what shows in its place. */
  placeholder?: string;
}) {
  const locale = useLocale() as "tr" | "en";
  const separators = localeSeparators(locale);
  const [focused, setFocused] = useState(false);
  // Padded only when nobody is actively typing -- padding on every keystroke
  // would rewrite "1500000.5" back to "1500000.50" before a further digit
  // could ever land after it.
  const display = toDisplay(
    !focused && allowDecimal ? padDecimals(value, MIN_DECIMALS) : value,
    separators,
  );
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    const input = e.target;
    const rawBefore = input.value;
    const caretBefore = input.selectionStart ?? rawBefore.length;
    const significantBefore = countSignificant(rawBefore, caretBefore, separators.decimal);

    const canonical = toCanonical(rawBefore, separators, allowDecimal);
    onChange(canonical);

    const nextDisplay = toDisplay(canonical, separators);
    // The value prop has not re-rendered yet on this tick, so the caret has
    // to be set against the display this keystroke produces, not against
    // `display` above (last render's value).
    requestAnimationFrame(() => {
      if (!inputRef.current) return;
      const pos = positionAfterSignificant(nextDisplay, significantBefore, separators.decimal);
      inputRef.current.setSelectionRange(pos, pos);
    });
  };

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
        ref={inputRef}
        type="text"
        inputMode={allowDecimal ? "decimal" : "numeric"}
        value={display}
        placeholder={placeholder}
        disabled={disabled}
        onChange={handleChange}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
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
        })}
      />
    </VuiBox>
  );
}

/** A whole-number field -- a term in months or days, an instalment count. */
export function IntegerField(
  props: Omit<Parameters<typeof AmountField>[0], "allowDecimal">,
) {
  return <AmountField {...props} allowDecimal={false} />;
}
