"use client";

import { Table as MuiTable, TableBody, TableContainer, TableRow } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { useLocale } from "next-intl";

import { Pill } from "@/components/ui/Pill";
import { VuiBox, VuiTypography } from "@/components/vision";
import type { CellValue, ResolvedColumn, Row } from "@/lib/contract";
import { formatDate, formatMoney, formatNumber, formatRate } from "@/lib/format";
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
}: {
  columns: ResolvedColumn[];
  rows: Row[];
  sort: SortState | null;
  onSort: (key: string) => void;
  bankLabels?: Record<string, string>;
  emptyLabel: string;
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
                  opacity={0.7}
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
                        color: active ? "info.main" : "inherit",
                        opacity: active ? 1 : "inherit",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "4px",
                      }}
                    >
                      {column.label.toUpperCase()}
                      <VuiBox component="span" sx={{ fontSize: "0.7em", opacity: active ? 1 : 0.45 }}>
                        {active ? (sort!.direction === "asc" ? "▲" : "▼") : "◆"}
                      </VuiBox>
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
              {columns.map((column) => (
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
                  }}
                >
                  <Cell
                    value={row.cells[column.key]}
                    column={column}
                    locale={locale}
                    bankLabels={bankLabels}
                  />
                </VuiBox>
              ))}
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
}: {
  value: CellValue | undefined;
  column: ResolvedColumn;
  locale: "tr" | "en";
  bankLabels?: Record<string, string>;
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
        <VuiTypography {...base} color="white">
          {typeof value === "number" ? formatNumber(value, locale) : String(value)}
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

    case "link":
      return (
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

/** A link reads better as its host than as 90 characters of path. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
