"use client";

import { Table as MuiTable, TableBody, TableContainer, TableRow, Tooltip } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import type { Theme } from "@mui/material/styles";
import { useLocale, useTranslations } from "next-intl";

import { AttachButton } from "@/components/chat/AttachButton";
import { Pill, type PillTone } from "@/components/ui/Pill";
import { VuiBox, VuiTypography } from "@/components/vision";
import type { CellValue, ResolvedColumn, Row } from "@/lib/contract";
import { BLANK_CELL, cellDisplayText, isBlankCell } from "@/lib/cell-display";
import { sortHint } from "@/lib/sort-hint";
import { TABLE_GUTTER, tableRowHoverSx, tableRule } from "@/lib/table-style";
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
  title,
  about,
  onAttachRow,
  onAttachTable,
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
  /**
   * What this table is, and what it is for.
   *
   * Neither is drawn -- the pages render their own headings, and adding a second
   * one inside the table would show the title twice. They are here so the table
   * can *say* what it is in the markup, which is the only way anything reading
   * the page afterwards can tell one table from another.
   *
   * That reader is the assistant. A quote lifted out of a cell arrives with the
   * row and the column already, but "which table" and "what is this table about"
   * were blank, and without them the agent has to ask the user what they are
   * looking at -- which defeats the point of attaching it. `about` is the
   * producer's own description (`subtitle`/`notes`), which is usually the one
   * sentence that explains what is being compared.
   *
   * Passed as data rather than inferred from nearby headings: the call site knows
   * the title exactly, and guessing it from the closest markup gets it wrong the
   * moment a page changes its layout.
   */
  title?: string;
  about?: string;
  /**
   * Hand this row, or this whole table, to the assistant.
   *
   * Both live here rather than being bolted on at the call sites, for the reason
   * this file already records about row hover: it used to be added by one caller
   * and so only one of four pages had it. A trailing column appears only when a
   * handler is passed, so a table that does not opt in is byte-for-byte unchanged.
   *
   * `onAttachRow` gets the row and its index because a `Row` has no id -- the
   * React key here is `cite_url` plus index -- so index is the only stable way to
   * say "that one".
   */
  onAttachRow?: (row: Row, index: number) => void;
  onAttachTable?: () => void;
}) {
  const locale = useLocale() as "tr" | "en";
  const theme = useTheme();
  const { grey } = theme.palette;
  const { size, fontWeightBold } = theme.typography;
  const { borderWidth } = theme.borders;

  // One decision, read in four places: the two header rows, the body cells, and
  // the group spans. Computing it per-site is how a header and its body get one
  // column out of step.
  // The assistant's own namespace, not the table's: these two labels name an
  // assistant action, and the strings for that live together.
  const tChat = useTranslations("chat");

  const hasActions = Boolean(onAttachRow || onAttachTable);

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
    <TableContainer
      sx={{ overflowX: "auto" }}
      // Read by `describeLocation` when something inside a cell is quoted. Empty
      // strings would answer "which table?" with "" and read as a fact, so the
      // attributes are omitted entirely when unknown.
      {...(title ? { "data-table-title": title } : {})}
      {...(about ? { "data-table-about": about } : {})}
    >
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
              {/* The group spans must add up to the number of columns, so an
                  extra column at the end needs an extra group to sit under or
                  the whole grouped header shifts left by one. */}
              {hasActions && <VuiBox component="th" aria-hidden data-no-outline="" />}
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
                  borderBottom={tableRule(theme)}
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
                          // Marked so anything reading the header as text can
                          // leave it out. Without this the column a quoted cell
                          // sits under came back as "INSTALMENT▲", and on a text
                          // column as "PRODUCT A–Z" -- the sort state welded onto
                          // the column's name.
                          data-sort-hint=""
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
            {hasActions && (
              <VuiBox
                component="th"
                pt={1.5}
                pb={1.25}
                textAlign="right"
                borderBottom={tableRule(theme)}
                // A column of controls, not data. Marked so the page outline the
                // assistant reads leaves it out rather than reporting an empty
                // trailing column on every row.
                data-no-outline=""
                sx={{ whiteSpace: "nowrap", px: GUTTER, width: 1 }}
              >
                {onAttachTable && (
                  <AttachButton
                    label={tChat("attachTable")}
                    onClick={onAttachTable}
                    // The table-level control is always visible. Row buttons
                    // appear on hover because there is one per row and forty of
                    // them at once is a column of clutter; there is only ever one
                    // of this, and a control nobody can see is a control nobody
                    // uses.
                    alwaysVisible
                  />
                )}
              </VuiBox>
            )}
          </TableRow>
        </VuiBox>

        <TableBody>
          {rows.map((row, index) => (
            <TableRow
              key={row.cite_url ? `${row.cite_url}-${index}` : index}
              // Row hover lives here, on the one component every table page
              // renders, rather than in an sx wrapper around it — which is how
              // it used to work, and why only /finansman had it while
              // /compare, /urunler and /kampanyalar did not.
              //
              // Scoped to the body row on purpose. A `MuiTableRow` override in
              // the theme would be tidier still, but `<thead>` rows are
              // `TableRow` too, so it would light up the headers as well.
              //
              // The tint is on the `td`, not the `tr`: a table row generates no
              // background box of its own under `border-collapse`, so painting
              // the row paints nothing. The cells are what is visible.
              sx={tableRowHoverSx}
            >
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
                      : tableRule(theme)
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
                    // A note on this one cell wins over the row's citation note:
                    // it was written about this value, and the row note is about
                    // where the row came from. Only the `link` cell falls back to
                    // the row note, which is the cell that citation belongs to.
                    title={row.cell_notes?.[column.key] ?? row.cite_note}
                    tone={row.cell_tones?.[column.key]}
                  />
                </VuiBox>
                );
              })}
              {hasActions && (
                <VuiBox
                  component="td"
                  py={1}
                  textAlign="right"
                  borderBottom={
                    index === rows.length - 1 ? null : tableRule(theme)
                  }
                  data-no-outline=""
                  sx={{ whiteSpace: "nowrap", px: GUTTER, lineHeight: 0 }}
                >
                  {onAttachRow && (
                    <AttachButton
                      label={tChat("attachRow")}
                      onClick={() => onAttachRow(row, index)}
                    />
                  )}
                </VuiBox>
              )}
            </TableRow>
          ))}
        </TableBody>
      </MuiTable>
    </TableContainer>
  );
}

/** The tones `Pill` knows. Anything else a producer sends is drawn neutral. */
const PILL_TONES = new Set<PillTone>(["neutral", "ok", "warn", "bad"]);

function Cell({
  value,
  column,
  locale,
  bankLabels,
  moved,
  best,
  title,
  tone,
}: {
  value: CellValue | undefined;
  column: ResolvedColumn;
  locale: "tr" | "en";
  bankLabels?: Record<string, string>;
  /** Set when this figure changed on the last refresh. */
  moved?: "up" | "down";
  /** Set when this figure is the best on its row. */
  best?: boolean;
  /** A note about this cell, or failing that the row's own `cite_note`. Shown
      as an instant tooltip on `link` and `badge` cells; other types ignore it. */
  title?: string;
  /** What this `badge` cell's state means, from the row's `cell_tones`. */
  tone?: string;
}) {
  const t = useTranslations("components");

  const base = {
    variant: "button" as const,
    fontWeight: "regular" as const,
    sx: { display: "inline-block", width: "max-content" },
  };

  // Absent is not zero and not false. A dash says "the producer did not find
  // this", which is a different fact from any value we could substitute.
  if (isBlankCell(value)) {
    return (
      <VuiTypography {...base} color="text" opacity={0.5}>
        {BLANK_CELL}
      </VuiTypography>
    );
  }

  // What the cell says. Shared with `cellDisplayText` rather than restated here,
  // because the assistant is now a second reader of these tables: when the two
  // drifted, an agent answered about "kuveytturk" while the user was looking at
  // "Kuveyt Türk". Only the *presentation* below is this component's own.
  const text = cellDisplayText(value, column, locale, bankLabels);

  switch (column.type) {
    case "money":
      return (
        <VuiTypography {...base} color="white">
          {text}
        </VuiTypography>
      );

    case "percent":
      return (
        <VuiTypography {...base} color="white">
          {text}
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
          {text}
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
          {text}
        </VuiTypography>
      );

    case "bank":
      return (
        <VuiTypography {...base} color="white" fontWeight="medium">
          {text}
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
          {/* The call to action, not the bare host. Every citation points at a
              deep page -- a specific campaign, a specific rate table -- and a
              link reading "vakifkatilim.com.tr" says the bank's front page,
              which is not where it goes. The host has not been dropped though:
              it moves into the tooltip below, because a reader is entitled to
              know which domain a link will take them to before they click. */}
          {t("citeLink")}
        </VuiTypography>
      );
      // The note only. The host is deliberately not shown anywhere -- not as
      // the link text and not in the tooltip -- because a domain on its own
      // reads as the bank's front page, which is never where a citation goes.
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
          {text}
        </VuiTypography>
      );

    case "badge": {
      // An unrecognised tone is drawn `neutral` rather than dropping the cell —
      // the same forgiveness `resolveTable` gives an unrecognised column type.
      const pill = <Pill tone={PILL_TONES.has(tone as PillTone) ? (tone as PillTone) : "neutral"}>{text}</Pill>;
      // Same instant tooltip the citation link uses, and for the same reason:
      // the note is what explains the chip, and a hover that takes the browser's
      // own second and a half to appear may as well not be there. Wrapped in a
      // span because `Pill` is a plain function component and cannot hold the
      // ref `Tooltip` hands its child.
      return title ? (
        <Tooltip title={title} arrow enterDelay={0} enterNextDelay={0} leaveDelay={0}>
          <span style={{ display: "inline-block" }}>{pill}</span>
        </Tooltip>
      ) : (
        pill
      );
    }

    default:
      return (
        <VuiTypography {...base} color="text">
          {text}
        </VuiTypography>
      );
  }
}

/**
 * The gutter between columns.
 *
 * Now the shared `TABLE_GUTTER` from `@/lib/table-style`, so the markdown table
 * the assistant produces lines its columns up with this one. Aliased rather than
 * inlined at the call sites to keep the diff honest about what changed.
 */
const GUTTER = TABLE_GUTTER;
