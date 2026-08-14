"use client";

import { Checkbox, Divider, Menu, MenuItem } from "@mui/material";
import { useTranslations } from "next-intl";
import { useState, type MouseEvent } from "react";

import { VuiBox, VuiButton, VuiInput, VuiTypography } from "@/components/vision";
import type { ResolvedColumn, Row } from "@/lib/contract";
import { distinctValues, filterKind, type FilterState } from "@/lib/table-filter";

/**
 * Filters built entirely from the table's own columns.
 *
 * There is no per-page filter code anywhere in the app, and there must not be:
 * the producer decides what columns exist, so anything hardcoded here would be
 * wrong for the next table it sends. A bank column gets a tick-list because it
 * is a bank column, not because this is the Finansman page.
 */
export function TableFilters({
  columns,
  rows,
  state,
  onChange,
}: {
  columns: ResolvedColumn[];
  /** The unfiltered rows — the tick-lists must offer values a filter hid. */
  rows: Row[];
  state: FilterState;
  onChange: (next: FilterState) => void;
  /**
   * Still passed by `TableWidget`, deliberately not destructured: the row count
   * they feed is unmounted (see below), and keeping them on the contract is
   * what makes re-mounting it a one-line change rather than a re-wiring.
   */
  matched: number;
  total: number;
}) {
  const t = useTranslations("components");

  const selects = columns.filter((c) => filterKind(c, rows) === "select");
  const ranges = columns.filter((c) => filterKind(c, rows) === "range");

  const toggleValue = (key: string, value: string) => {
    const current = state.values[key] ?? [];
    onChange({
      ...state,
      values: {
        ...state.values,
        [key]: current.includes(value)
          ? current.filter((v) => v !== value)
          : [...current, value],
      },
    });
  };

  const setBound = (key: string, edge: "min" | "max", raw: string) => {
    const bounds = { ...(state.ranges[key] ?? {}) };
    if (raw === "") delete bounds[edge];
    else bounds[edge] = Number(raw);
    onChange({ ...state, ranges: { ...state.ranges, [key]: bounds } });
  };

  const toggleColumn = (key: string) =>
    onChange({
      ...state,
      hidden: state.hidden.includes(key)
        ? state.hidden.filter((k) => k !== key)
        : [...state.hidden, key],
    });

  const active =
    state.search !== "" ||
    Object.values(state.values).some((v) => v.length > 0) ||
    Object.values(state.ranges).some((r) => r.min !== undefined || r.max !== undefined);

  return (
    <VuiBox mb={2}>
      <VuiBox display="flex" flexWrap="wrap" alignItems="center" gap="10px" mb={1.5}>
        {/* The free-text search is unmounted, not deleted: `FilterState.search`
            and the matching branch in `applyFilters` (with its Turkish folding
            and its tests) are all still here, so re-mounting is putting a
            VuiInput back in this slot. The per-column filters cover the same
            ground with less ambiguity about what is being matched. */}

        {selects.map((column) => (
          <SelectFilter
            key={column.key}
            label={column.label}
            options={distinctValues(rows, column.key)}
            chosen={state.values[column.key] ?? []}
            onToggle={(value) => toggleValue(column.key, value)}
            onSetAll={(next) =>
              onChange({ ...state, values: { ...state.values, [column.key]: next } })
            }
          />
        ))}

        {ranges.map((column) => (
          <RangeFilter
            key={column.key}
            label={column.label}
            bounds={state.ranges[column.key] ?? {}}
            minLabel={t("min")}
            maxLabel={t("max")}
            onChange={(edge, raw) => setBound(column.key, edge, raw)}
          />
        ))}

        <SelectFilter
          label={t("columns")}
          options={columns.map((c) => c.label)}
          // Inverted on purpose: the menu shows which columns are *visible*,
          // while the state stores which are hidden.
          chosen={columns.filter((c) => !state.hidden.includes(c.key)).map((c) => c.label)}
          onToggle={(label) => {
            const column = columns.find((c) => c.label === label);
            if (column) toggleColumn(column.key);
          }}
          onSetAll={(next) => {
            // A table with every column switched off is not a view of anything,
            // and the only way back would be this same menu. So "clear" leaves
            // the first column standing rather than emptying the table.
            const visible = next.length === 0 ? [columns[0].label] : next;
            onChange({
              ...state,
              hidden: columns.filter((c) => !visible.includes(c.label)).map((c) => c.key),
            });
          }}
          badgeCount={state.hidden.length}
        />
      </VuiBox>

      <VuiBox display="flex" alignItems="center" gap="12px">
        {/* The row count is unmounted, not deleted: `matched` and `total` are
            still passed in and the `rowCount` / `rowCountFiltered` strings are
            still in both message files, so re-mounting is one VuiTypography.
            The filter buttons already carry their own counts — "Banka (8)" —
            so a filter that did something is visible without it. */}
        {active && (
          <VuiTypography
            component="button"
            variant="caption"
            color="info"
            onClick={() =>
              onChange({ search: "", values: {}, ranges: {}, hidden: state.hidden })
            }
            sx={{
              background: "none",
              border: "none",
              cursor: "pointer",
              textDecoration: "underline",
              padding: 0,
            }}
          >
            {t("clearFilters")}
          </VuiTypography>
        )}
      </VuiBox>
    </VuiBox>
  );
}

function SelectFilter({
  label,
  options,
  chosen,
  onToggle,
  onSetAll,
  badgeCount,
}: {
  label: string;
  options: string[];
  chosen: string[];
  onToggle: (value: string) => void;
  /** Replace the whole selection at once — see the select-all row below. */
  onSetAll: (next: string[]) => void;
  badgeCount?: number;
}) {
  const t = useTranslations("components");
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const count = badgeCount ?? chosen.length;
  const allChosen = options.length > 0 && chosen.length === options.length;

  return (
    <>
      <VuiButton
        variant={count > 0 ? "contained" : "outlined"}
        color={count > 0 ? "info" : "white"}
        size="small"
        onClick={(e: MouseEvent<HTMLElement>) => setAnchor(e.currentTarget)}
      >
        {label}
        {count > 0 ? ` (${count})` : ""}
      </VuiButton>

      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { maxHeight: 320 } } }}
      >
        {/* One control that flips, rather than a "select all" sitting next to a
            "clear" where only one of them ever does anything. Its label states
            what the click will do, not what the current state is. */}
        <MenuItem onClick={() => onSetAll(allChosen ? [] : [...options])} dense>
          <Checkbox
            checked={allChosen}
            indeterminate={chosen.length > 0 && !allChosen}
            size="small"
            sx={{ p: 0.5, mr: 1 }}
          />
          <VuiTypography variant="button" color="white" fontWeight="medium">
            {allChosen ? t("clearAll") : t("selectAll")}
          </VuiTypography>
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />

        {options.map((option) => (
          <MenuItem key={option} onClick={() => onToggle(option)} dense>
            <Checkbox checked={chosen.includes(option)} size="small" sx={{ p: 0.5, mr: 1 }} />
            <VuiTypography variant="button" color="white" fontWeight="regular">
              {option}
            </VuiTypography>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

function RangeFilter({
  label,
  bounds,
  minLabel,
  maxLabel,
  onChange,
}: {
  label: string;
  bounds: { min?: number; max?: number };
  minLabel: string;
  maxLabel: string;
  onChange: (edge: "min" | "max", raw: string) => void;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const set = bounds.min !== undefined || bounds.max !== undefined;

  return (
    <>
      <VuiButton
        variant={set ? "contained" : "outlined"}
        color={set ? "info" : "white"}
        size="small"
        onClick={(e: MouseEvent<HTMLElement>) => setAnchor(e.currentTarget)}
      >
        {label}
      </VuiButton>

      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>
        <VuiBox px={2} py={1} display="flex" flexDirection="column" gap="8px" minWidth="180px">
          {(["min", "max"] as const).map((edge) => (
            <VuiBox key={edge} display="flex" alignItems="center" justifyContent="space-between" gap="8px">
              <VuiTypography variant="caption" color="text">
                {edge === "min" ? minLabel : maxLabel}
              </VuiTypography>
              <VuiInput
                type="number"
                size="small"
                value={bounds[edge] ?? ""}
                onChange={(e: { target: { value: string } }) => onChange(edge, e.target.value)}
              />
            </VuiBox>
          ))}
        </VuiBox>
      </Menu>
    </>
  );
}
