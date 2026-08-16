"use client";

import { Checkbox, Divider, Menu, MenuItem } from "@mui/material";
import { useState, type MouseEvent } from "react";

import { ActionButton } from "@/components/ui/ActionButton";
import { VuiBox, VuiTypography } from "@/components/vision";

export interface MultiSelectOption {
  value: string;
  label: string;
}

/**
 * A tick-list behind a button, with a select-all row.
 *
 * The one multi-select in the app — every button that opens a menu of
 * checkboxes with a select-all row goes through this, whether the list is a
 * board's fixed pairs/banks/sides or the comparator's bank picker. Two
 * page-specific copies of the same control (one on the FX board, one on the
 * comparator) drifted independently and only one of them got a bug fix,
 * which is the failure mode this file exists to rule out: a second copy is a
 * second set of edge cases to keep in step, and it will not be kept in step.
 *
 * The trigger shows `chosen / total` rather than the values, because three of
 * these sit in a row and a list of ticked pairs would wrap the whole bar — and
 * plain "All" when nothing is excluded, since "27 / 27" makes the reader do
 * arithmetic to learn that no filter is on.
 */
export function MultiSelect({
  label,
  options,
  selected,
  onChange,
  allLabel,
  allSelectedLabel,
  disabledOptions = [],
}: {
  label: string;
  options: MultiSelectOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** The select-all row inside the menu. */
  allLabel: string;
  /** Shown on the trigger when nothing is excluded. */
  allSelectedLabel: string;
  /**
   * Shown below a divider, greyed out and unclickable — an option that
   * exists but cannot be chosen, with its own label explaining why (the
   * comparator's ineligible banks). Shown, not hidden: a bank missing from
   * the list looks like an omission, while a disabled one is an answer.
   */
  disabledOptions?: MultiSelectOption[];
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const all = selected.length === options.length;

  return (
    <VuiBox>
      <VuiBox mb={0.75}>
        <VuiTypography variant="caption" color="text">
          {label}
        </VuiTypography>
      </VuiBox>

      <ActionButton
        variant="outlined"
        color="white"
        onClick={(e: MouseEvent<HTMLElement>) => setAnchor(e.currentTarget)}
      >
        {all ? allSelectedLabel : `${selected.length} / ${options.length}`}
      </ActionButton>

      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { maxHeight: 360 } } }}
      >
        <MenuItem
          dense
          onClick={() => onChange(all ? [] : options.map((o) => o.value))}
        >
          <Checkbox
            checked={all}
            indeterminate={selected.length > 0 && !all}
            size="small"
            sx={{ p: 0.5, mr: 1 }}
          />
          <VuiTypography variant="button" color="white" fontWeight="medium">
            {allLabel}
          </VuiTypography>
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />

        {options.map((option) => (
          <MenuItem
            key={option.value}
            dense
            onClick={() =>
              onChange(
                selected.includes(option.value)
                  ? selected.filter((v) => v !== option.value)
                  : [...selected, option.value],
              )
            }
          >
            <Checkbox
              checked={selected.includes(option.value)}
              size="small"
              sx={{ p: 0.5, mr: 1 }}
            />
            <VuiTypography variant="button" color="white" fontWeight="regular">
              {option.label}
            </VuiTypography>
          </MenuItem>
        ))}

        {disabledOptions.length > 0 && <Divider sx={{ my: 0.5 }} />}
        {disabledOptions.map((option) => (
          <MenuItem key={option.value} dense disabled sx={{ opacity: 0.5 }}>
            <VuiBox sx={{ width: 30 }} />
            <VuiTypography variant="button" color="text" fontWeight="regular">
              {option.label}
            </VuiTypography>
          </MenuItem>
        ))}
      </Menu>
    </VuiBox>
  );
}
