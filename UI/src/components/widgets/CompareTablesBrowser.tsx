"use client";

import Card from "@mui/material/Card";
import CircularProgress from "@mui/material/CircularProgress";
import Grid from "@mui/material/Grid";
import Skeleton from "@mui/material/Skeleton";
import type { Theme } from "@mui/material/styles";
import Tooltip from "@mui/material/Tooltip";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import {
  IoAlertCircleOutline,
  IoArrowForward,
  IoDocumentTextOutline,
  IoMegaphone,
  IoOpenOutline,
  IoPricetags,
} from "react-icons/io5";

import { ActionButton } from "@/components/ui/ActionButton";
import { CenteredState } from "@/components/ui/CenteredState";
import { Dropdown } from "@/components/ui/Dropdown";
import { Pill } from "@/components/ui/Pill";
import { SearchField } from "@/components/ui/SearchField";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api, type TableDetailOut, type TableSummary } from "@/lib/api";
import { resolveTable, type TableProps } from "@/lib/contract";
import { capitalize } from "@/lib/format";
import { applyFilters, EMPTY_FILTERS, sortRows, type FilterState } from "@/lib/table-filter";
import { searchTables } from "@/lib/table-search";
import { useBankLabels } from "@/lib/use-bank-labels";
import { useTableSort } from "@/lib/use-table-sort";

import { useAttachTable } from "@/lib/chat/use-attach-table";
import { ProducedTable } from "./ProducedTable";
import { TableOverview } from "./TableOverview";
import { TableFilters } from "./TableFilters";

/** Same glyphs `vision/routes.js` uses for these two nav entries — the
    picker header repeats the sidebar's own icon so the two stay linked. */
const CATEGORY_ICON = { ürün: IoPricetags, kampanya: IoMegaphone } as const;

type RowOut = TableDetailOut["rows"][number];

/** The column key for the citation link — same hardcoded-Turkish-label
    convention the backend already uses for `Banka`, `Geçerlilik` and `Bitiş`
    (`compare_tables.py`), since this whole data domain has no English
    translation to draw from. */
const KAYNAK_KEY = "Kaynak";

/**
 * Splits one table's rows into banks worth comparing and banks that do not
 * offer it at all — the same shape `Comparator` uses for banks it could not
 * price (`unavailable`, rendered in their own card below the results table
 * rather than as blank cells inside it).
 *
 * The verdict is `row.offers`, decided by the API. It used to be decided here,
 * by checking whether every populated cell said `sunulmuyor` — which was true
 * of the previous dataset and is true of only 88 rows in the current one. The
 * producer now answers the question in a column it names itself, and it has
 * used 93 different names for that column across the pool, so the check moved
 * to the one place that can hold that knowledge without shipping it to the
 * browser. `null` is not `false`: a row nothing settled stays in the table.
 */
function splitRows(
  rows: RowOut[],
): { offering: RowOut[]; absent: { bank: string; cite_url?: string; cite_note?: string }[] } {
  const offering: RowOut[] = [];
  const absent: { bank: string; cite_url?: string; cite_note?: string }[] = [];
  for (const row of rows) {
    if (row.offers === false) {
      absent.push({
        bank: String(row.cells.Banka ?? ""),
        cite_url: row.cite_url ?? undefined,
        cite_note: row.cite_note ?? undefined,
      });
      continue;
    }
    offering.push(row);
  }
  return { offering, absent };
}

/**
 * `TableDetailOut`'s optional fields come back as `null` (Pydantic's
 * `str | None = None`) where `TableProps` (`@/lib/contract`, a Zod schema)
 * only allows `undefined` — the two schemas agree on shape but not on how
 * "absent" is spelled. Narrowed once here rather than at the spread site.
 *
 * Every row already carries the page the pipeline read it from (`cite_url`)
 * — until now used only as a React key, never shown. Appended here as its
 * own `Kaynak` column of the contract's existing `link` type so a user can
 * open the bank's own page and check the figures for themselves, reusing
 * `ProducedTable`'s already-built link rendering rather than inventing a
 * second way to show a URL.
 *
 * The `Banka` column is marked `sortable` explicitly, the same override
 * `lib/comparator.ts`'s own `col()` helper applies to every `bank`-type
 * column it builds (`sortable: numeric || type === "date" || type ===
 * "bank"`). `resolveTable`'s generic default list (money/percent/number/date)
 * does not include `bank`, because most producers never send one — but this
 * table always does, and alphabetical-by-bank is exactly the sort Comparator
 * itself offers for free on every one of its tables.
 *
 * The `Geçerlilik` and `Bitiş` columns need no override at all, which is why
 * the API types them the way it does: `resolveTable` makes a `badge` filterable
 * and a `date` sortable on its own, so "show me only the live ones" and "order
 * by when they end" both fall out of the generic machinery rather than out of
 * anything written on this page.
 */
function toTableProps(detail: TableDetailOut, rows: RowOut[]): TableProps {
  return {
    id: detail.id,
    title: detail.title,
    subtitle: detail.subtitle ?? undefined,
    notes: detail.notes ?? undefined,
    columns: [
      ...detail.columns.map((c) => ({
        key: c.key,
        label: c.label ?? undefined,
        type: c.type ?? undefined,
        sortable: c.type === "bank" ? true : undefined,
      })),
      { key: KAYNAK_KEY, label: KAYNAK_KEY, type: "link" },
    ],
    rows: rows.map((r) => ({
      cells: { ...r.cells, [KAYNAK_KEY]: r.cite_url ?? null },
      cite_url: r.cite_url ?? undefined,
      cite_note: r.cite_note ?? undefined,
      cell_notes: r.cell_notes ?? undefined,
      cell_tones: r.cell_tones ?? undefined,
      offers: r.offers ?? undefined,
    })),
  };
}

/**
 * Browse -> pick -> view, for one category of the offline comparison-table
 * pool (`dataprep.compare`, `data/_tables/*.json`). Two pages mount this,
 * one per category — the fixed two-value split the pipeline itself enforces
 * on every table it creates, so there is no third value to plan for here.
 *
 * Three states in one component rather than three routes: narrowing by
 * subcategory and picking a table are the same "which table" question, and
 * splitting it across pages would mean round-tripping the category/subcategory
 * choice through the URL for no benefit — nothing here is worth bookmarking
 * on its own.
 */
export function CompareTablesBrowser({ category }: { category: "ürün" | "kampanya" }) {
  const t = useTranslations("compareTables");
  const tc = useTranslations("components");
  const locale = useLocale() as "tr" | "en";
  const [subcategory, setSubcategory] = useState<string>("");
  // Free text over the picker grid, not over any table's rows -- `TableFilters`
  // does the second job once a table is open. Held here rather than inside
  // `SearchField` because the count beside it and the empty state below both
  // have to agree with it.
  const [query, setQuery] = useState("");
  const [tableId, setTableId] = useState<string | null>(null);
  // Local sort state, reset per table. The three-click asc/desc/off toggle is
  // `useTableSort`, the same hook `Comparator` and `TableWidget` call, so this
  // table is driven by the exact same mechanism rather than a lookalike -- it
  // used to be a hand-copied one. The state stays out here and not inside
  // `ProducedTable` because only this component knows when it has to go:
  // opening a different table.
  const { sort, toggleSort, resetSort } = useTableSort();
  /**
   * Row filters, reset per table.
   *
   * This page used to hold a hand-built bank `MultiSelect` of its own and no
   * other filter. `TableFilters` builds one tick-list per column that earns one
   * — which for these tables is exactly two, the `bank` column and the
   * `badge` validity column — so the hand-built picker was a second component
   * doing the first half of that job, and the second half did not exist. The
   * new validity filter is the reason it had to go either way: two pickers
   * side by side, one generic and one not, is the drift this app has a rule
   * against.
   */
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);

  const list = useQuery({
    queryKey: ["compare-tables", category],
    queryFn: () => api.compareTablesList(category),
  });

  const detail = useQuery({
    queryKey: ["compare-table", tableId],
    queryFn: () => api.compareTable(tableId as string),
    enabled: tableId !== null,
  });

  const bankLabels = useBankLabels();

  const inSubcategory = useMemo<TableSummary[]>(() => {
    const tables = list.data?.tables ?? [];
    return subcategory ? tables.filter((table) => table.subcategory === subcategory) : tables;
  }, [list.data, subcategory]);

  // Two steps, kept apart: the subcategory picker decides what the pool is and
  // the keyword box narrows it, so a search that matches nothing can say so
  // without the page pretending the subcategory is empty.
  const filtered = useMemo<TableSummary[]>(
    () => searchTables(inSubcategory, query, locale),
    [inSubcategory, query, locale],
  );

  const subcategoryOptions = useMemo(
    () => [
      { value: "", label: t("allSubcategories") },
      ...(list.data?.subcategories ?? []).map((s) => ({ value: s, label: capitalize(s, locale) })),
    ],
    [list.data, t, locale],
  );

  const { offering, absent } = useMemo(
    () => (detail.data ? splitRows(detail.data.rows) : { offering: [], absent: [] }),
    [detail.data],
  );

  // The same normalisation `Comparator` gets for free from a hand-built
  // table object: column type/align/sortable inference, nothing else.
  const table = useMemo(
    () => (detail.data ? resolveTable(toTableProps(detail.data, offering)) : null),
    [detail.data, offering],
  );

  // Filter, then sort — the same order and the same two functions `TableWidget`
  // and `Comparator` use. The tick-lists themselves are built from the columns
  // by `TableFilters`, so nothing here names a bank or a validity value.
  const rows = useMemo(() => {
    if (!table) return [];
    const matched = applyFilters(table.rows, table.columns, filters, locale);
    return sortRows(matched, sort, table.columns, locale, bankLabels);
  }, [table, filters, sort, locale, bankLabels]);
  /**
   * What this table is, for anything reading the page afterwards.
   *
   * Neither is drawn -- the card header above already shows the title, and a
   * second one inside the table would print it twice. They travel with anything
   * the user attaches, so the assistant knows which table a row came from and
   * what that table compares. `subtitle` is the producer's one-line description
   * and `notes` its caveat; an agent answering about a figure wants both.
   */
  const tableTitle = detail.data ? capitalize(detail.data.title, locale) : undefined;
  const tableAbout =
    [detail.data?.subtitle, detail.data?.notes].filter(Boolean).join(" — ") || undefined;

  // Above the early return below: hooks cannot be called conditionally. Given the
  // filtered, sorted rows, so what is attached is what is on screen.
  const attach = useAttachTable({
    columns: table?.columns ?? [],
    rows,
    title: tableTitle,
    about: tableAbout,
    bankLabels,
  });

  const openTable = (id: string) => {
    resetSort();
    setFilters(EMPTY_FILTERS);
    setTableId(id);
  };

  if (tableId) {
    return (
      <VuiBox display="flex" flexDirection="column" gap="24px">
        {/* Above the table, not inside it: it answers "is this worth reading"
            before the reader has to work that out from the columns. `ready`
            gates the screenshot on the rows actually being on screen -- a
            capture taken while the table is still loading shows the model a
            spinner and asks it what the table means. */}
        <TableOverview
          // Keyed, so opening a second table gets a second component rather
          // than the first one's state: the generation result and its error
          // both live in the card, and without this the previous table's
          // overview stays on screen under the new table's heading.
          key={tableId}
          tableId={tableId}
          ready={Boolean(table) && !detail.isLoading}
        />

        <Card>
          <VuiBox display="flex" alignItems="center" justifyContent="space-between" mb="16px" flexWrap="wrap" gap="12px">
            <VuiTypography variant="lg" color="white">
              {detail.data ? capitalize(detail.data.title, locale) : t("loadingTable")}
            </VuiTypography>
            <ActionButton variant="outlined" color="white" onClick={() => setTableId(null)}>
              {t("backToList")}
            </ActionButton>
          </VuiBox>

          {/* One tick-list per column that earns one, built from the columns
              themselves: `Banka`, and the validity chip. Given the *unfiltered*
              rows on purpose -- a tick-list that only offered the values still
              showing could never be used to widen a selection back out. */}
          {table && (
            <VuiBox mb={2}>
              <TableFilters
                columns={table.columns}
                rows={table.rows}
                state={filters}
                onChange={setFilters}
                bankLabels={bankLabels}
                matched={rows.length}
                total={table.rows.length}
              />
            </VuiBox>
          )}

          {detail.isLoading && <CenteredState icon={<CircularProgress size={28} color="info" />} label={t("loadingTable")} />}
          {detail.isError && (
            <CenteredState icon={<IoAlertCircleOutline size="28px" />} label={t("loadFailed")} tone="error" />
          )}
          {table && (
            <ProducedTable
              columns={table.columns}
              rows={rows}
              sort={sort}
              onSort={toggleSort}
              bankLabels={bankLabels}
              emptyLabel={tc("tableEmpty")}
              title={tableTitle}
              about={tableAbout}
              onAttachRow={attach.onAttachRow}
              onAttachTable={attach.onAttachTable}
            />
          )}
        </Card>

        {/* Split out, not shown as a row of dashes inside the table above —
            the same treatment `Comparator` gives banks it could not price.
            Mounted only once there is a real gap to explain. */}
        {absent.length > 0 && (
          <Card>
            <VuiBox mb={2}>
              <VuiTypography variant="lg" color="white">
                {t(category === "ürün" ? "notOfferedTitleUrun" : "notOfferedTitleKampanya")}
              </VuiTypography>
            </VuiBox>
            {/* Marked so the page outline carries it: each line here is under
                the outline's minimum text length, so without this the whole
                card is invisible to anything reading the page as text -- which
                is how the overview agent stopped knowing which banks do not
                offer the product. */}
            <VuiBox
              display="flex"
              flexDirection="column"
              gap="10px"
              data-outline-list={t(
                category === "ürün" ? "notOfferedTitleUrun" : "notOfferedTitleKampanya",
              )}
            >
              {absent.map(({ bank, cite_url, cite_note }) => (
                <VuiBox key={bank} display="flex" gap="10px" alignItems="center" flexWrap="wrap">
                  <VuiTypography variant="button" color="white" fontWeight="medium">
                    {bankLabels[bank] ?? bank}
                  </VuiTypography>
                  <Pill tone="neutral">{t("notOfferedPill")}</Pill>
                  {/* Even a "not offered" verdict came from a page the
                      pipeline actually read — worth linking so it can be
                      checked, the same as any other citation in the table.
                      `Tooltip` with `enterDelay={0}`, same as the table's own
                      citation links — the browser's native `title` delay is
                      not ours to set, and a note that takes seconds to
                      appear may as well not be there. */}
                  {cite_url && (
                    <Tooltip title={cite_note ?? ""} arrow enterDelay={0} enterNextDelay={0} leaveDelay={0}>
                      <VuiTypography
                        component="a"
                        href={cite_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        variant="caption"
                        color="info"
                        sx={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "4px",
                          textDecoration: "underline",
                        }}
                      >
                        <IoOpenOutline size="12px" />
                        {/* Same reasoning as `ProducedTable`'s link cell: the
                            bare host reads as the bank's front page when every
                            citation is a deep link. The host stays reachable in
                            the tooltip. */}
                        {tc("citeLink")}
                      </VuiTypography>
                    </Tooltip>
                  )}
                </VuiBox>
              ))}
            </VuiBox>
          </Card>
        )}
      </VuiBox>
    );
  }

  const CategoryIcon = CATEGORY_ICON[category];

  return (
    <VuiBox display="flex" flexDirection="column" gap="24px">
      <Card>
        <VuiBox display="flex" alignItems="center" gap="10px" mb="22px">
          <VuiBox color="white" display="flex">
            <CategoryIcon size="20px" />
          </VuiBox>
          <VuiTypography variant="lg" color="white">
            {t(category === "ürün" ? "titleUrun" : "titleKampanya")}
          </VuiTypography>
        </VuiBox>

        <Dropdown
          label={t("subcategory")}
          value={subcategory}
          options={subcategoryOptions}
          onChange={setSubcategory}
          minWidth="18rem"
          fullWidth={false}
        />
      </Card>

      <Card>
        {list.isLoading && (
          <Grid container spacing={3}>
            {Array.from({ length: 6 }).map((_, i) => (
              <Grid item xs={12} sm={6} lg={4} key={i}>
                <Skeleton variant="rounded" height={132} sx={{ borderRadius: "12px" }} />
              </Grid>
            ))}
          </Grid>
        )}
        {list.isError && (
          <CenteredState icon={<IoAlertCircleOutline size="28px" />} label={t("loadFailed")} tone="error" />
        )}
        {list.data && inSubcategory.length === 0 && (
          <CenteredState icon={<IoDocumentTextOutline size="28px" />} label={t("noTablesMatch")} />
        )}

        {list.data && inSubcategory.length > 0 && (
          <>
            {/* Count and search share the row: the count is what the search
                changes, and reading "3 tablo" next to the box that produced
                the 3 is the whole feedback loop. */}
            <VuiBox
              mb="16px"
              display="flex"
              alignItems="center"
              justifyContent="space-between"
              gap="12px"
              flexWrap="wrap"
            >
              <VuiTypography variant="caption" color="text">
                {t("tableCount", { count: filtered.length })}
              </VuiTypography>
              <SearchField
                value={query}
                onChange={setQuery}
                label={t("searchTables")}
                placeholder={t("searchPlaceholder")}
                clearLabel={t("clearSearch")}
              />
            </VuiBox>

            {/* The search box stays on screen when nothing matches -- it is
                mounted above this branch, not inside the grid -- so a query
                that finds nothing can be edited rather than started over. */}
            {filtered.length === 0 ? (
              <CenteredState
                icon={<IoDocumentTextOutline size="28px" />}
                label={t("noTablesMatchSearch", { query })}
              />
            ) : (
              <Grid container spacing={3}>
                {filtered.map((summary) => (
                  <Grid item xs={12} sm={6} lg={4} key={summary.id}>
                    <TableCard table={summary} locale={locale} onClick={() => openTable(summary.id)} />
                  </Grid>
                ))}
              </Grid>
            )}
          </>
        )}
      </Card>
    </VuiBox>
  );
}

/** One clickable card in the picker grid — topic, a two-line docstring
    excerpt, and the subcategory as plain caption text underneath. */
function TableCard({
  table,
  locale,
  onClick,
}: {
  table: TableSummary;
  locale: "tr" | "en";
  onClick: () => void;
}) {
  return (
    <Card
      onClick={onClick}
      sx={(theme: Theme) => ({
        height: "100%",
        padding: "18px",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        transition: "transform 150ms ease, box-shadow 150ms ease",
        "&:hover": {
          transform: "translateY(-2px)",
          boxShadow: theme.shadows[8],
        },
      })}
    >
      <VuiBox display="flex" alignItems="flex-start" justifyContent="space-between" gap="8px">
        <VuiTypography variant="button" color="white" fontWeight="bold" sx={{ flex: 1 }}>
          {capitalize(table.topic, locale)}
        </VuiTypography>
        <VuiBox color="text" opacity={0.5} mt="2px">
          <IoArrowForward size="16px" />
        </VuiBox>
      </VuiBox>

      <VuiTypography
        variant="caption"
        color="text"
        sx={{
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {table.docstring}
      </VuiTypography>

      <VuiBox mt="auto" pt="4px">
        <VuiTypography variant="caption" color="text" opacity={0.7}>
          {capitalize(table.subcategory, locale)}
        </VuiTypography>
      </VuiBox>
    </Card>
  );
}
