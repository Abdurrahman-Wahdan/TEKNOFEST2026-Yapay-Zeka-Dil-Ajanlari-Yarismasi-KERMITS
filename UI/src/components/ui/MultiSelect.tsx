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
 * Lifted out of the comparator's bank picker rather than written again: that
 * control had the shape every filter here needs — a count on the trigger, one
 * row per option, and an all/none row whose checkbox goes indeterminate on a
 * partial selection. A second copy would have been a second set of edge cases
 * to keep in step.
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
}: {
  label: string;
  options: MultiSelectOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** The select-all row inside the menu. */
  allLabel: string;
  /** Shown on the trigger when nothing is excluded. */
  allSelectedLabel: string;
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
      </Menu>
    </VuiBox>
  );
}
