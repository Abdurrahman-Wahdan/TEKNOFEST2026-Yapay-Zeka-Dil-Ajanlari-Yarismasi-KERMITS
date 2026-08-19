"use client";

import Card from "@mui/material/Card";
import CircularProgress from "@mui/material/CircularProgress";
import Grid from "@mui/material/Grid";
import Skeleton from "@mui/material/Skeleton";
import type { Theme } from "@mui/material/styles";
import Tooltip from "@mui/material/Tooltip";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState, type ReactNode } from "react";
import {
  IoAlertCircleOutline,
  IoArrowForward,
  IoDocumentTextOutline,
  IoMegaphone,
  IoOpenOutline,
  IoPricetags,
} from "react-icons/io5";

import { ActionButton } from "@/components/ui/ActionButton";
import { Dropdown } from "@/components/ui/Dropdown";
import { MultiSelect } from "@/components/ui/MultiSelect";
import { Pill } from "@/components/ui/Pill";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api, type TableDetailOut, type TableSummary } from "@/lib/api";
import { resolveTable, type TableProps } from "@/lib/contract";
import { capitalize } from "@/lib/format";
import { sortRows } from "@/lib/table-filter";
import { useBankLabels } from "@/lib/use-bank-labels";
import { useTableSort } from "@/lib/use-table-sort";

import { useAttachTable } from "@/lib/chat/use-attach-table";
import { ProducedTable } from "./ProducedTable";

/** Same glyphs `vision/routes.js` uses for these two nav entries — the
    picker header repeats the sidebar's own icon so the two stay linked. */
const CATEGORY_ICON = { ürün: IoPricetags, kampanya: IoMegaphone } as const;

type RowOut = TableDetailOut["rows"][number];

/**
 * The pipeline's two sentinel strings — a field it found nothing for
 * (`belirtilmemiş`, "not specified") and a bank that does not offer this
 * product/campaign at all (`sunulmuyor`, "not offered"). Matched trimmed and
 * Turkish-lowercased, the same fold `lib/format.ts` uses for bank/product
 * names, so stray capitalisation or whitespace in the source data cannot slip
 * one through as ordinary content.
 */
const NOT_OFFERED = "sunulmuyor";
const NOT_SPECIFIED = "belirtilmemiş";

/** The column key for the citation link — same hardcoded-Turkish-label
    convention the backend already uses for `Banka` (`compare_tables.py`),
    since this whole data domain has no English translation to draw from. */
const KAYNAK_KEY = "Kaynak";

function fold(value: string): string {
  return value.trim().toLocaleLowerCase("tr-TR");
}

/** True once every populated field in this bank's row says the same single
    thing: nothing is offered. A row like that carries no information to
    compare, so it is split out rather than rendered as a row of dashes. */
function isFullyUnoffered(cells: RowOut["cells"]): boolean {
  const values = Object.entries(cells)
    .filter(([key]) => key !== "Banka")
    .map(([, v]) => v);
  const present = values.filter((v) => v !== null && v !== undefined && v !== "");
  return present.length > 0 && present.every((v) => typeof v === "string" && fold(v) === NOT_OFFERED);
}

/**
 * Splits one table's rows into banks worth comparing and banks that do not
 * offer it at all — the same shape `Comparator` uses for banks it could not
 * price (`unavailable`, rendered in their own card below the results table
 * rather than as blank cells inside it). A row that mixes real content with
 * an occasional `sunulmuyor`/`belirtilmemiş` field stays in the table, with
 * just that field nulled out to the contract's existing "producer did not
 * find this" dash — the fact still shown, just not invented text.
 */
function splitRows(
  rows: RowOut[],
): { offering: RowOut[]; absent: { bank: string; cite_url?: string; cite_note?: string }[] } {
  const offering: RowOut[] = [];
  const absent: { bank: string; cite_url?: string; cite_note?: string }[] = [];
  for (const row of rows) {
    if (isFullyUnoffered(row.cells)) {
      absent.push({
        bank: String(row.cells.Banka ?? ""),
        cite_url: row.cite_url ?? undefined,
        cite_note: row.cite_note ?? undefined,
      });
      continue;
    }
    const cells = Object.fromEntries(
      Object.entries(row.cells).map(([key, value]) => {
        if (key === "Banka" || typeof value !== "string") return [key, value];
        const folded = fold(value);
        return folded === NOT_OFFERED || folded === NOT_SPECIFIED ? [key, null] : [key, value];
      }),
    ) as RowOut["cells"];
    offering.push({ ...row, cells });
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
  // Reusing `comparator`'s own strings for the bank picker below (`Bankalar`,
  // `Tümünü seç`, `Tümü`) rather than adding a second set of translations for
  // the same three words.
  const tComp = useTranslations("comparator");
  const locale = useLocale() as "tr" | "en";
  const [subcategory, setSubcategory] = useState<string>("");
  const [tableId, setTableId] = useState<string | null>(null);
  // Local sort state, reset per table. The three-click asc/desc/off toggle is
  // `useTableSort`, the same hook `Comparator` and `TableWidget` call, so this
  // table is driven by the exact same mechanism rather than a lookalike -- it
  // used to be a hand-copied one. The state stays out here and not inside
  // `ProducedTable` because only this component knows when it has to go:
  // opening a different table.
  const { sort, toggleSort, resetSort } = useTableSort();
  // `null` means "every bank offering this table" -- the same convention
  // `Comparator` uses for its own bank selection, so "nothing excluded yet"
  // does not have to be recomputed as an explicit list of every value.
  const [selectedBanks, setSelectedBanks] = useState<string[] | null>(null);

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

  const filtered = useMemo<TableSummary[]>(() => {
    const tables = list.data?.tables ?? [];
    return subcategory ? tables.filter((table) => table.subcategory === subcategory) : tables;
  }, [list.data, subcategory]);

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

  // The banks actually in this table's rows -- not the full bank registry,
  // since a table with 3 offering banks should offer a 3-item picker, not
  // one padded with 7 banks that would immediately show empty if ticked.
  const bankOptions = useMemo(() => {
    if (!table) return [];
    const seen = new Set<string>();
    const options: { value: string; label: string }[] = [];
    for (const row of table.rows) {
      const key = String(row.cells.Banka ?? "");
      if (key && !seen.has(key)) {
        seen.add(key);
        options.push({ value: key, label: bankLabels[key] ?? key });
      }
    }
    return options.sort((a, b) => a.label.localeCompare(b.label, locale === "tr" ? "tr" : "en"));
  }, [table, bankLabels, locale]);
  const chosenBanks = selectedBanks ?? bankOptions.map((o) => o.value);

  const rows = useMemo(() => {
    if (!table) return [];
    const visible = table.rows.filter((r) => chosenBanks.includes(String(r.cells.Banka ?? "")));
    return sortRows(visible, sort, table.columns, locale, bankLabels);
  }, [table, chosenBanks, sort, locale, bankLabels]);
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
    setSelectedBanks(null);
    setTableId(id);
  };

  if (tableId) {
    return (
      <VuiBox display="flex" flexDirection="column" gap="24px">
        <Card>
          <VuiBox display="flex" alignItems="center" justifyContent="space-between" mb="16px" flexWrap="wrap" gap="12px">
            <VuiTypography variant="lg" color="white">
              {detail.data ? capitalize(detail.data.title, locale) : t("loadingTable")}
            </VuiTypography>
            <ActionButton variant="outlined" color="white" onClick={() => setTableId(null)}>
              {t("backToList")}
            </ActionButton>
          </VuiBox>

          {/* Same component Comparator uses for its own bank picker --
              select-all row, tick individual banks, "chosen / total" on the
              trigger. There, it decides who gets queried; here, it narrows
              which of the already-loaded rows show, but it is the identical
              control either way. */}
          {bankOptions.length > 1 && (
            <VuiBox mb={2}>
              <MultiSelect
                label={tComp("banks")}
                options={bankOptions}
                selected={chosenBanks}
                onChange={setSelectedBanks}
                allLabel={tComp("allBanks")}
                allSelectedLabel={tComp("allSelected")}
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
            <VuiBox display="flex" flexDirection="column" gap="10px">
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
        {list.data && filtered.length === 0 && (
          <CenteredState icon={<IoDocumentTextOutline size="28px" />} label={t("noTablesMatch")} />
        )}

        {list.data && filtered.length > 0 && (
          <>
            <VuiBox mb="16px">
              <VuiTypography variant="caption" color="text">
                {t("tableCount", { count: filtered.length })}
              </VuiTypography>
            </VuiBox>
            <Grid container spacing={3}>
              {filtered.map((summary) => (
                <Grid item xs={12} sm={6} lg={4} key={summary.id}>
                  <TableCard table={summary} locale={locale} onClick={() => openTable(summary.id)} />
                </Grid>
              ))}
            </Grid>
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

/** A centred icon + label for empty/loading/error states — the app has no
    dedicated empty-state component, so this covers all three inline. */
function CenteredState({
  icon,
  label,
  tone = "default",
}: {
  icon: ReactNode;
  label: string;
  tone?: "default" | "error";
}) {
  return (
    <VuiBox
      display="flex"
      flexDirection="column"
      alignItems="center"
      justifyContent="center"
      gap="10px"
      py="40px"
      color={tone === "error" ? "error" : "text"}
      sx={{ opacity: tone === "error" ? 1 : 0.7 }}
    >
      {icon}
      <VuiTypography variant="button" color={tone === "error" ? "error" : "text"}>
        {label}
      </VuiTypography>
    </VuiBox>
  );
}
