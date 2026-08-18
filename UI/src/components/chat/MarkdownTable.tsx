"use client";

import { Table as MuiTable, TableBody, TableContainer, TableRow } from "@mui/material";
import { useTheme } from "@mui/material/styles";

import { VuiBox } from "@/components/vision";
import { TABLE_GUTTER, tableRowHoverSx, tableRule } from "@/lib/table-style";

import { domProps, type El } from "./markdown-dom";

/**
 * The assistant's markdown tables, drawn in the app's table style.
 *
 * These are the overrides handed to Streamdown's `components` prop, so a
 * `| a | b |` in the agent's answer comes out looking like every other table in
 * the app -- MUI table primitives, the shared rule colour, the uppercase
 * micro-headers, the same column gutter -- instead of Streamdown's own
 * Tailwind-prose defaults.
 *
 * A second renderer beside `ProducedTable` rather than a reuse of it, because the
 * two have genuinely different inputs: `ProducedTable` needs typed
 * `ResolvedColumn[]` and a sort handler, and a markdown table has arbitrary
 * string headers, string cells and no types at all. The *style* is shared through
 * `@/lib/table-style`, which is the part that must not drift.
 */

export function MdTable(props: El<"table">) {
  // The scroll container is the table's own, not the message column's. A wide
  // table must not widen the conversation -- that turns every message in the
  // thread into a horizontal scroll.
  return (
    <TableContainer sx={{ overflowX: "auto", my: 2 }}>
      <MuiTable {...domProps(props)} />
    </TableContainer>
  );
}

export function MdThead(props: El<"thead">) {
  return <VuiBox component="thead" {...domProps(props)} />;
}

export function MdTbody(props: El<"tbody">) {
  return <TableBody {...domProps(props)} />;
}

export function MdTr(props: El<"tr">) {
  const theme = useTheme();
  // Applied to header rows too, harmlessly: a `<thead>` row has no `td`, and the
  // hover rule only ever targets `td`.
  return <TableRow sx={tableRowHoverSx(theme)} {...domProps(props)} />;
}

/** Markdown's `|:---|---:|` arrives as an inline text-align, so it is honoured. */
function alignOf(style: React.CSSProperties | undefined) {
  return (style?.textAlign as "left" | "center" | "right") ?? "left";
}

export function MdTh(props: El<"th">) {
  const theme = useTheme();
  const { size, fontWeightBold } = theme.typography;

  return (
    <VuiBox
      component="th"
      pt={1.5}
      pb={1.25}
      textAlign={alignOf(props.style)}
      fontSize={size.xxs}
      fontWeight={fontWeightBold}
      color="text"
      opacity={0.7}
      borderBottom={tableRule(theme)}
      sx={{ whiteSpace: "nowrap", px: TABLE_GUTTER }}
      {...domProps(props)}
    />
  );
}

export function MdTd(props: El<"td">) {
  const theme = useTheme();

  return (
    <VuiBox
      component="td"
      py={1}
      textAlign={alignOf(props.style)}
      borderBottom={tableRule(theme)}
      color="white"
      fontSize={theme.typography.size.sm}
      // Cells wrap, unlike `ProducedTable`'s. That table's cells are figures and
      // labels, which must never break; a markdown cell can hold a sentence, and
      // `nowrap` on a sentence forces the whole table into a horizontal scroll.
      sx={{ px: TABLE_GUTTER, verticalAlign: "top" }}
      {...domProps(props)}
    />
  );
}
