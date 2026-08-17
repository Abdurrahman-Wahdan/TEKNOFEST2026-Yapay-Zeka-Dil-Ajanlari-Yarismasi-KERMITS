"use client";

import { Table as MuiTable, TableBody, TableContainer, TableRow, Tooltip } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import type { Theme } from "@mui/material/styles";
import { useLocale } from "next-intl";

import { Pill } from "@/components/ui/Pill";
import { VuiBox, VuiTypography } from "@/components/vision";
import type { CellValue, ResolvedColumn, Row } from "@/lib/contract";
import { formatDate, formatMoney, formatNumber, formatRate, hostOf } from "@/lib/format";
import { sortHint } from "@/lib/sort-hint";
import type { SortState } from "@/lib/table-filter";

/**
 * An AI-produced table, drawn in the app's own table style.
 *
 * Deliberately built from the same pieces as `examples/Tables/Table` — MUI's
 * table primitives wrapped in VuiBox/VuiTypography, the same grey[700] rules,
 * the same uppercase micro-headers — so a produced table is indistinguishable
 * from a hand-built one. It is a separate component only because the template's
 * Table calls `name.toUpperCase()` on its headers, which rules out putting a
 * sort button in one.
 *
 * Everything about the *shape* still comes from the data: the producer decides
 * how many columns there are and what they are called.
 */
export function ProducedTable({
  columns,
  rows,
  sort,
  onSort,
  bankLabels,
  emptyLabel,
  movements,
  best,
  rowKey,
  groups,
}: {
  columns: ResolvedColumn[];
  rows: Row[];
  sort: SortState | null;
  onSort: (key: string) => void;
  bankLabels?: Record<string, string>;
  emptyLabel: string;
  /**
   * `"<rowKey>|<column>" -> "up" | "down"` for cells that moved since the last
   * refresh. A live board that restates a price silently is a board nobody
   * reads, so the cell is tinted the way a trading screen does it. Absent for
   * a cell that did not move, so nothing is drawn for the still ones.
   */
  movements?: Record<string, "up" | "down">;
  /**
   * `"<rowKey>|<column>"` for the figures that win their row. On a board of
   * mixed instruments this is the only price comparison that means anything:
   * across a row, six banks quoting the same thing.
   */
  best?: Record<string, true>;
  /** Which cell identifies a row for `movements` and `best`. */
  rowKey?: string;
  /**
   * An optional header row above the columns, each entry spanning `span` of
   * them. The FX board needs it: a bank owns a buy and a sell column, and two
   * separate headings repeating the bank's name reads as two banks rather than
   * one bank's pair.
   *
   * Spans must add up to `columns.length`; a filler entry with an empty label
   * covers the columns that belong to no group.
   */
  groups?: { key: string; label: string; span: number }[];
}) {
  const locale = useLocale() as "tr" | "en";
  const { grey } = useTheme().palette;
  const { size, fontWeightBold } = useTheme().typography;
  const { borderWidth } = useTheme().borders;

  if (columns.length === 0 || rows.length === 0) {
    return (
      <VuiBox py={3}>
        <VuiTypography variant="button" color="text" fontWeight="regular">
          {emptyLabel}
        </VuiTypography>
      </VuiBox>
    );
  }

  return (
    <TableContainer sx={{ overflowX: "auto" }}>
      <MuiTable>
        <VuiBox component="thead">
          {groups && groups.length > 0 && (
            <TableRow>
              {groups.map((group) => (
                <VuiBox
                  key={group.key}
                  component="th"
                  colSpan={group.span}
                  pt={1.5}
                  pb={0.75}
                  // Left, not centred over the pair. Centred, a bank's name
                  // floated between its two columns and lined up with neither,
                  // so reading down from the name did not land on its prices.
                  textAlign="left"
                  fontSize={size.xxs}
                  fontWeight={fontWeightBold}
                  color={group.label ? "white" : "text"}
                  borderBottom={
                    group.label ? `${borderWidth[1]} solid ${grey[600]}` : null
                  }
                  sx={{ whiteSpace: "nowrap", px: GUTTER }}
                >
                  {group.label}
                </VuiBox>
              ))}
            </TableRow>
          )}
          <TableRow>
            {columns.map((column) => {
              const active = sort?.key === column.key;
              return (
                <VuiBox
                  key={column.key}
                  component="th"
                  pt={1.5}
                  pb={1.25}
                  textAlign={column.align}
                  fontSize={size.xxs}
                  fontWeight={fontWeightBold}
                  color="text"
                  // Only the static headings are dimmed. A sortable one is a
                  // control, and this opacity used to multiply with the one on
                  // the indicator inside it -- 0,7 x 0,35 left the marker at
                  // about a quarter visible, which is why it read as absent.
                  opacity={column.sortable ? 1 : 0.7}
                  borderBottom={`${borderWidth[1]} solid ${grey[700]}`}
                  sx={{
                    whiteSpace: "nowrap",
                    px: GUTTER,
                  }}
                >
                  {column.sortable ? (
                    <VuiBox
                      component="button"
                      type="button"
                      onClick={() => onSort(column.key)}
                      sx={{
                        background: "none",
                        border: "none",
                        padding: 0,
                        font: "inherit",
                        letterSpacing: "inherit",
                        cursor: "pointer",
                        color: active ? "info.main" : "text.main",
                        opacity: 1,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "4px",
                      }}
                    >
                      {column.label.toUpperCase()}
                      {/* Only when a sort is applied. A marker on every
                          heading, active or not, cannot be told apart from one
                          that means something. */}
                      {active && (
                        <VuiBox
                          component="span"
                          sx={(theme: Theme) => ({
                            fontSize: "1em",
                            fontWeight: 700,
                            color: theme.palette.info.main,
                            whiteSpace: "nowrap",
                          })}
                        >
                          {sortHint(column, sort!.direction)}
                        </VuiBox>
                      )}
                    </VuiBox>
                  ) : (
                    column.label.toUpperCase()
                  )}
                </VuiBox>
              );
            })}
          </TableRow>
        </VuiBox>

        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={row.cite_url ? `${row.cite_url}-${index}` : index}>
              {columns.map((column) => {
                const cellKey = rowKey
                  ? `${String(row.cells[rowKey] ?? "")}|${column.key}`
                  : "";
                const moved = movements?.[cellKey];
                const isBest = Boolean(best?.[cellKey]);
                return (
                <VuiBox
                  key={column.key}
                  component="td"
                  py={1}
                  textAlign={column.align}
                  borderBottom={
                    index === rows.length - 1
                      ? null
                      : `${borderWidth[1]} solid ${grey[700]}`
                  }
                  sx={{
                    whiteSpace: "nowrap",
                    px: GUTTER,
                    // The cell's own invisible line-height strut sits below
                    // its content and is not symmetric with the padding, so
                    // `vertical-align: middle` centres against the strut, not
                    // the visible content -- a pill ends up a couple of
                    // pixels low. Zeroing it here lets the padding alone
                    // decide the cell's height.
                    lineHeight: 0,
                  }}
                >
                  <Cell
                    value={row.cells[column.key]}
                    column={column}
                    locale={locale}
                    bankLabels={bankLabels}
                    moved={moved}
                    best={isBest}
                    title={row.cite_note}
                  />
                </VuiBox>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </MuiTable>
    </TableContainer>
  );
}

function Cell({
  value,
  column,
  locale,
  bankLabels,
  moved,
  best,
  title,
}: {
  value: CellValue | undefined;
  column: ResolvedColumn;
  locale: "tr" | "en";
  bankLabels?: Record<string, string>;
  /** Set when this figure changed on the last refresh. */
  moved?: "up" | "down";
  /** Set when this figure is the best on its row. */
  best?: boolean;
  /** The row's own `cite_note`, if any — shown as a native hover title on a
      `link`-type cell only; every other cell type ignores it. */
  title?: string;
}) {
  const base = {
    variant: "button" as const,
    fontWeight: "regular" as const,
    sx: { display: "inline-block", width: "max-content" },
  };

  // Absent is not zero and not false. A dash says "the producer did not find
  // this", which is a different fact from any value we could substitute.
  if (value === undefined || value === null || value === "") {
    return (
      <VuiTypography {...base} color="text" opacity={0.5}>
        —
      </VuiTypography>
    );
  }

  switch (column.type) {
    case "money":
      return (
        <VuiTypography {...base} color="white">
          {typeof value === "number" ? formatMoney(value, locale, column.currency) : String(value)}
        </VuiTypography>
      );

    case "percent":
      return (
        <VuiTypography {...base} color="white">
          {typeof value === "number" ? formatRate(value, locale) : String(value)}
        </VuiTypography>
      );

    case "number":
      return (
        <VuiTypography
          {...base}
          // The figure itself carries the movement, not the cell behind it: a
          // whole cell washed green reads as a highlighted row, and on a board
          // where several prices move at once the table turns into a traffic
          // light. Colouring the number is what a trading screen does.
          color={moved ? (moved === "up" ? "success" : "error") : "white"}
          // Weight, not colour: the colour is already saying whether the price
          // moved, and a second colour on the same figure would leave the
          // reader guessing which meaning applied.
          fontWeight={best ? "bold" : "regular"}
          sx={{ transition: "color 900ms ease-out" }}
        >
          {typeof value === "number"
            ? formatNumber(value, locale, column.decimals)
            : String(value)}
          {moved && (
            <VuiBox
              component="span"
              aria-hidden
              sx={{ ml: 0.5, fontSize: "0.85em", lineHeight: 1 }}
            >
              {moved === "up" ? "▲" : "▼"}
            </VuiBox>
          )}
        </VuiTypography>
      );

    case "date":
      return (
        <VuiTypography {...base} color="text">
          {formatDate(String(value), locale)}
        </VuiTypography>
      );

    case "bank":
      return (
        <VuiTypography {...base} color="white" fontWeight="medium">
          {bankLabels?.[String(value)] ?? String(value)}
        </VuiTypography>
      );

    case "link": {
      // The native `title` attribute puts the browser's own hover delay in
      // charge -- Chrome, Firefox and Safari each pick their own, and none
      // of them can be told to show sooner. `Tooltip` with `enterDelay={0}`
      // shows the instant the cursor lands, which a citation link needs: the
      // note is the only thing that says *why* the source supports this row.
      const link = (
        <VuiTypography
          {...base}
          component="a"
          href={String(value)}
          target="_blank"
          rel="noopener noreferrer"
          color="info"
          sx={{ ...base.sx, textDecoration: "underline" }}
        >
          {hostOf(String(value))}
        </VuiTypography>
      );
      return title ? (
        <Tooltip title={title} arrow enterDelay={0} enterNextDelay={0} leaveDelay={0}>
          {link}
        </Tooltip>
      ) : (
        link
      );
    }

    case "bool":
      // A definite "no" must not render as the same glyph as "we don't know" —
      // both would be a dash, and absent and false are different facts.
      return (
        <VuiTypography {...base} color={value === true ? "success" : "text"}>
          {value === true ? "✓" : "✕"}
        </VuiTypography>
      );

    case "badge":
      return <Pill>{String(value)}</Pill>;

    default:
      return (
        <VuiTypography {...base} color="text">
          {String(value)}
        </VuiTypography>
      );
  }
}

/**
 * The gutter between columns, applied identically to headers and cells.
 *
 * Only the *inner* spacing. The outer edges — the first column's left and the
 * last column's right — belong to the table theme
 * (`assets/theme/components/table/tableContainer`), which sets them with
 * `!important` for every table in the app so they cannot drift per table. The
 * template's own Table derives padding from `align` instead, which is how a
 * header on 24px ended up above a cell on 8px in the same column.
 */
const GUTTER = 1.5;
