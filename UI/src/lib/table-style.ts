import type { Theme } from "@mui/material/styles";

/**
 * The app's table look, in one place.
 *
 * There are two table *renderers* in the app, because there are two table data
 * contracts. `ProducedTable` draws a typed, sortable board from
 * `ResolvedColumn[]` -- money columns, bank columns, citation links. The
 * assistant's markdown tables have none of that: arbitrary string headers,
 * string cells, arriving a row at a time mid-stream. Pushing markdown through
 * `ProducedTable` would mean inventing column types it does not have and passing
 * a sort handler that does nothing.
 *
 * What the one-table rule is actually protecting is the *look*, and that is what
 * lives here: both renderers read these, so a change to the rule colour or the
 * column gutter lands on every table in the app at once. Neither renderer is
 * allowed a table style of its own.
 */

/**
 * The gutter between columns, applied identically to headers and cells.
 *
 * Only the *inner* spacing. The outer edges belong to the table theme
 * (`assets/theme/components/table/tableContainer`), which sets them with
 * `!important` for every table in the app so they cannot drift per table.
 */
export const TABLE_GUTTER = 1.5;

/** The rule between rows, and under the header. */
export function tableRule(theme: Theme): string {
  return `${theme.borders.borderWidth[1]} solid ${theme.palette.grey[700]}`;
}

/**
 * The uppercase micro-header every table in the app uses.
 *
 * Returned as props rather than `sx` because `VuiBox` takes `fontSize` and
 * `fontWeight` as its own props, and mixing the two spellings across the two
 * renderers is how they would drift apart.
 */
export function tableHeaderProps(theme: Theme) {
  return {
    fontSize: theme.typography.size.xxs,
    fontWeight: theme.typography.fontWeightBold,
    borderBottom: tableRule(theme),
  } as const;
}

/**
 * Row hover, as an `sx` fragment for a `<tbody>` row.
 *
 * The tint goes on the `td`, not the `tr`: under `border-collapse` a row
 * generates no background box of its own, so painting the row paints nothing.
 * The cells are what is visible.
 */
export function tableRowHoverSx(theme: Theme) {
  return {
    "& td": { transition: "background-color 150ms ease" },
    "&:hover td": { backgroundColor: theme.palette.surfaces.hover },
  } as const;
}
