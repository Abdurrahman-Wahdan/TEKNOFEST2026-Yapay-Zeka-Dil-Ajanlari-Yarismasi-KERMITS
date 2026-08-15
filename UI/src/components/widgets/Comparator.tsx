"use client";

import Card from "@mui/material/Card";
import { Checkbox, Divider, Menu, MenuItem } from "@mui/material";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState, type MouseEvent } from "react";

import { ActionButton } from "@/components/ui/ActionButton";
import { Dropdown } from "@/components/ui/Dropdown";
import { Pill } from "@/components/ui/Pill";
import { NumberField, TextField } from "@/components/ui/TextField";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api, type Bank, type Comparison, type Rate } from "@/lib/api";
import {
  CATEGORIES,
  cardTable,
  convertTable,
  defaultSort,
  financeTable,
  mileRatesTable,
  profitShareTable,
  rateGroup,
  movements,
  ratesBoard,
  type BankRate,
  type CategoryKey,
  type Labels,
} from "@/lib/comparator";
import type { Row } from "@/lib/contract";
import type { FilterState, SortState } from "@/lib/table-filter";
import { hiddenColumns } from "@/lib/board-filter";
import { useRatesStream, type StreamedBoard } from "@/lib/use-rates-stream";
import { applyFilters, EMPTY_FILTERS, sortRows } from "@/lib/table-filter";

import { ProducedTable } from "./ProducedTable";
import { BoardFilters } from "./BoardFilters";

/**
 * How often the FX board refreshes.
 *
 * The browser's interval, not the banks'. `api/cache.py` holds each bank's
 * board for a few seconds, so this can be short without the banks seeing it --
 * three seconds reads as live and still gives a cell that flashed time to be
 * noticed before the next poll lands.
 */
const RATES_POLL_MS = 3_000;

/** A submitted run. The tag picks the endpoint; `params` keeps its own types. */
type RunQuery =
  | { kind: "finance"; params: Parameters<typeof api.compareFinance>[0] }
  | { kind: "profit_share"; params: Parameters<typeof api.compareProfitShare>[0] }
  | { kind: "convert"; params: Parameters<typeof api.compareExchange>[0] }
  | { kind: "card"; bank: string; params: Parameters<typeof api.cardQuote>[1] };

/** Categories priced per bank rather than ranked across a shared family. */
const SINGLE_BANK_CATEGORIES: CategoryKey[] = ["card", "miles"];

/**
 * The live comparison tool.
 *
 * Everything here is deterministic software over the bank endpoints — no model
 * is involved, and no figure is computed by us except the two that are labelled
 * as ours (a derived conversion, a spread).
 *
 * The shape of the page follows what the endpoints actually allow:
 *
 *  - **A category first**, because the six kinds of comparison take different
 *    inputs and reach different banks. Seven banks price financing, six price
 *    participation accounts, three publish rates, one publishes miles.
 *  - **Banks second**, with the ones that cannot take part greyed out and their
 *    own reason attached, rather than silently absent.
 *  - **Then inputs, bounded by the selection.** Dünya's konut product stops at
 *    84 months; with Dünya selected the term cannot be set past it, and the
 *    ceiling says which bank set it. This is the difference between a form that
 *    can only ask answerable questions and the one that let someone request 360
 *    months and watch every bank decline.
 */
export function Comparator() {
  const t = useTranslations("comparator");
  const locale = useLocale();
  const tc = useTranslations("common");

  const [category, setCategory] = useState<CategoryKey>("finance");
  const [selected, setSelected] = useState<string[] | null>(null);
  const [family, setFamily] = useState("konut-yeni");
  const [amount, setAmount] = useState("1000000");
  const [term, setTerm] = useState("120");
  const [currency, setCurrency] = useState("TRY");
  const [source, setSource] = useState("USD");
  const [target, setTarget] = useState("TRY");
  // Card and miles are priced per bank -- each bank sells its own cards, so
  // there is no shared family to rank the way finance and profit_share have.
  // A dedicated single-bank selection, not the multi-bank picker used below.
  const [singleBank, setSingleBank] = useState<string | null>(null);
  const [cardCode, setCardCode] = useState<string | null>(null);
  const [installments, setInstallments] = useState("6");
  const [sort, setSort] = useState<SortState | null>(null);
  // Free text plus per-column tick-lists. The board is 32 rows across six
  // banks, so "where is the Qatari riyal" is a search, not a scroll.
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  // A discriminated union rather than a loose bag: the three comparison calls
  // take different parameters, and the tag is what lets the query function pick
  // the right one without casting away the types the client already has.
  const [query, setQuery] = useState<RunQuery | null>(null);

  const { data: banks } = useQuery({ queryKey: ["banks"], queryFn: api.banks });
  const { data: familyList } = useQuery({ queryKey: ["families"], queryFn: api.families });

  const spec = CATEGORIES[category];


  /** Banks that can serve this category at all, and why the others cannot. */
  const eligible = useMemo(
    () => (banks ?? []).filter((b) => b.publishes.includes(spec.capability)),
    [banks, spec.capability],
  );
  const eligibleKeys = eligible.map((b) => b.name);
  const chosen = selected ?? eligibleKeys;
  const activeBanks = chosen.filter((b) => eligibleKeys.includes(b));
  // Which banks publish a rate feed is the registry's answer, not a list kept
  // here: a bank that starts publishing appears on the board without a release.
  const rateBanks = (banks ?? [])
    .filter((b) => b.publishes.includes("rates"))
    .map((b) => b.name)
    .filter((name) => activeBanks.includes(name));

  // The one bank a card/miles selection resolves to. Not derived from
  // `activeBanks`: those come from the multi-select picker used by the other
  // four categories, and forcing card/miles through the same control would let
  // someone "select" two banks for a quote that only ever prices one.
  const effectiveSingleBank =
    singleBank && eligibleKeys.includes(singleBank) ? singleBank : (eligibleKeys[0] ?? "");

  const { data: cardCatalog } = useQuery({
    queryKey: ["cardProducts", effectiveSingleBank],
    queryFn: () => api.bankProducts(effectiveSingleBank, "card"),
    enabled: category === "card" && Boolean(effectiveSingleBank),
  });
  const effectiveCardCode =
    cardCode && cardCatalog?.some((p) => p.code === cardCode)
      ? cardCode
      : (cardCatalog?.[0]?.code ?? "");

  // The mile-rate table is read-only reference data, like the rates board —
  // it loads on selecting a bank rather than waiting for a submit, because
  // there is nothing to submit: no amount, no term, just the published table.
  const { data: mileRates } = useQuery({
    queryKey: ["mileRates", effectiveSingleBank],
    queryFn: () => api.mileRates(effectiveSingleBank),
    enabled: category === "miles" && Boolean(effectiveSingleBank),
    staleTime: 60 * 60 * 1000,
  });

  const families = useMemo(
    () =>
      (familyList ?? []).filter((f) =>
        category === "profit_share" ? f.category === "profit_share" : f.category === "finance",
      ),
    [familyList, category],
  );

  // Constraints are read from cached catalogues, so this re-runs freely as the
  // bank selection changes without costing a bank request each time.
  const needsConstraints = category === "finance" || category === "profit_share";
  const { data: constraints } = useQuery({
    queryKey: ["constraints", category, family, activeBanks],
    queryFn: () =>
      api.constraints({
        family,
        category: category as "finance" | "profit_share",
        banks: activeBanks,
      }),
    enabled: needsConstraints && Boolean(family) && activeBanks.length > 0,
  });

  const bounds = constraints?.intersection;

  // Nothing fetches until the user submits. Each run is a live fan-out to real
  // bank WAFs, so it is never triggered by typing.
  const result = useQuery({
    queryKey: ["comparison", category, query],
    queryFn: async (): Promise<Comparison> => {
      if (!query) throw new Error("No query.");
      if (query.kind === "finance") return api.compareFinance(query.params);
      if (query.kind === "profit_share") return api.compareProfitShare(query.params);
      if (query.kind === "convert") return api.compareExchange(query.params);
      throw new Error(`${query.kind} does not run through the fan-out endpoint.`);
    },
    enabled: query !== null && (query.kind === "finance" || query.kind === "profit_share" || query.kind === "convert"),
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });

  const cardQuoteResult = useQuery({
    queryKey: ["cardQuote", query],
    queryFn: () => {
      if (!query || query.kind !== "card") throw new Error("No query.");
      return api.cardQuote(query.bank, query.params);
    },
    enabled: query !== null && query.kind === "card",
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });

  // The rate board is the one category that loads without a submit, and the one
  // thing here that moves on its own, so it polls rather than waiting to be
  // asked. The interval is the browser's; the banks never see it. `api/cache.py`
  // holds each bank's board for a few seconds, so every tab on any interval
  // costs one request per TTL -- which matters because two of these boards are
  // page reads and Albaraka's WAF fingerprints the TLS handshake.
  // The socket is the live path; this stays as the fallback for a proxy that
  // will not upgrade, and stops polling the moment the stream is up.
  const stream = useRatesStream(category === "rates");

  const rateQueries = useQueries({
    queries: rateBanks.map((bank) => ({
      queryKey: ["rates", bank],
      queryFn: () => api.bankRates(bank),
      enabled: category === "rates" && !stream.live,
      refetchInterval: category === "rates" && !stream.live ? RATES_POLL_MS : false,
      // Polling in the background would keep six banks warm for a tab nobody
      // is looking at; the first refetch on return brings it current anyway.
      refetchIntervalInBackground: false,
      staleTime: RATES_POLL_MS,
      refetchOnWindowFocus: true,
    })),
  });

  // The currencies the chosen family can actually be priced in, and the one
  // that will be sent. Held separately from `currency` for the same reason as
  // `effectiveSingleBank`: the family can change under a selection that is no
  // longer offered, and a select showing XAU while the state still says TRY
  // sends TRY. That is exactly what happened to the gold family -- the picker
  // read correctly and the request asked for the wrong currency.
  const currencyOptions = bounds?.currencies?.length ? bounds.currencies : ["TRY"];
  const effectiveCurrency = currencyOptions.includes(currency)
    ? currency
    : currencyOptions[0];

  // Which stated bound the current inputs actually break.
  //
  // The pills already said "up to 3.000 XAU" and "from 92 days" while the form
  // happily submitted 10.000 over 30, so Kuveyt Türk came back as "not
  // available for this amount or term" — reading as a gap at the bank when it
  // was the question being unanswerable. The point of reading constraints up
  // front is that the form cannot ask something no bank can answer.
  const broken = (() => {
    const out = new Set<string>();
    if (!bounds || category === "rates" || category === "miles") return out;
    const value = Number(amount);
    const months = Number(term);
    if (Number.isFinite(value) && value > 0) {
      if (bounds.min_amount != null && value < bounds.min_amount) out.add("min_amount");
      if (bounds.max_amount != null && value > bounds.max_amount) out.add("max_amount");
    }
    if (category !== "convert" && Number.isFinite(months) && months > 0) {
      if (bounds.min_term != null && months < bounds.min_term) out.add("min_term");
      if (bounds.max_term != null && months > bounds.max_term) out.add("max_term");
    }
    return out;
  })();

  const bankNames = Object.fromEntries(
    (banks ?? []).map((b) => [b.name, b.display_name]),
  );

  const labels: Labels = {
    bank: t("bank"), instalment: t("instalment"), total: t("total"),
    profitRate: t("profitRate"), annualCost: t("annualCost"), product: t("product"),
    term: t("term"), netProfit: t("netProfit"), grossProfit: t("grossProfit"),
    netAnnual: t("netAnnual"), termUnit: t("termUnit"), currency: t("currency"),
    instrument: t("instrument"), buy: t("buy"), sell: t("sell"), spread: t("spread"),
    spreadPct: t("spreadPct"), asOf: t("asOf"), result: t("result"), rate: t("rate"),
    source: t("bankOwn"), computed: t("computed"), card: t("card"),
    installments: t("installments"), tier: t("tier"), category: t("category_label"),
    perLira: t("perLira"), basis: t("basis"), insured: t("insured"),
    unit: t("unit"), perUnit: t("perUnit"), perGram: t("perGram"),
    perCoin: t("perCoin"),
    uninsured: t("uninsured"), campaign: t("campaign"), coversAll: t("coversAll"), rateOnly: t("rateOnly"),
  };

  // Not hand-memoized: the dependencies here are derived arrays, which the
  // React Compiler cannot verify and refuses to optimize around. Building a
  // table from at most a few dozen rows is nothing; the compiler handles it.
  const table = (() => {
    if (category === "rates") {
      const collected: BankRate[] = [];
      // Whichever path has the data. The shapes are identical -- the stream
      // sends the same RateOut rows the REST route returns -- so the board is
      // built the same way either way.
      if (stream.live) {
        for (const [bank, board] of Object.entries(stream.banks) as [string, StreamedBoard][]) {
          for (const r of board.rates) {
            if (rateGroup(r) === "parity") continue;
            collected.push({ ...r, bank });
          }
        }
      } else {
        rateQueries.forEach((q, i) => {
          for (const r of (q.data ?? []) as Rate[]) {
            if (rateGroup(r) === "parity") continue; // a cross rate is not a TRY price
            collected.push({ ...r, bank: rateBanks[i] });
          }
        });
      }
      const live = [...new Set(collected.map((r) => r.bank))];
      return ratesBoard(collected, live.length ? live : rateBanks, labels, bankNames);
    }
    if (category === "miles") {
      if (!mileRates) return null;
      return mileRatesTable(mileRates, effectiveSingleBank, labels);
    }
    if (category === "card") {
      if (!cardQuoteResult.data) return null;
      return cardTable(cardQuoteResult.data, labels);
    }
    const c = result.data;
    if (!c) return null;
    if (category === "finance") return financeTable(c, labels);
    if (category === "profit_share") return profitShareTable(c, labels);
    if (category === "convert") return convertTable(c, labels);
    return null;
  })();

  // The user's choice when they have made one, otherwise the category's own
  // ranking. Passed to the table as well as to the sort, so the header shows
  // which column the answer is ordered by rather than looking unsorted.
  const effectiveSort = sort ?? defaultSort(category);

  // What moved since the previous poll.
  //
  // Compared during render against state, not in an effect: React's own
  // "adjust state when the input changes" pattern. The two alternatives both
  // fail — reading a ref during render is rejected because a discarded
  // concurrent render would leave the board diffing against a poll nobody
  // saw, and diffing inside an effect means setting state from an effect,
  // which re-renders every poll for a value that was already knowable.
  //
  // Keyed on a signature of the values: `table` is rebuilt every render, so
  // comparing the object would fire on renders where nothing changed.
  const boardRows = category === "rates" ? table?.rows : undefined;
  const signature = boardRows
    ? boardRows.map((r) => Object.values(r.cells).join(",")).join("|")
    : "";

  // The movements are stored with the rows, not computed beside them: a
  // render-phase setState makes React throw this render away and run again, so
  // anything worked out here and kept in a local would never reach the screen.
  const [seen, setSeen] = useState<
    { signature: string; rows: Row[]; moved: Record<string, "up" | "down"> } | null
  >(null);
  if (boardRows && seen?.signature !== signature) {
    setSeen({
      signature,
      rows: boardRows,
      moved: movements(seen?.rows ?? null, boardRows),
    });
  }
  const moved = seen?.signature === signature ? seen.moved : {};

  // The board's two column filters are computed from what was picked rather
  // than stored as hidden keys, so neither can wipe the other out.
  const boardBanks =
    category === "rates" && table && "banks" in table
      ? (table.banks as string[])
      : [];
  const hidden =
    category === "rates" ? hiddenColumns(filters, boardBanks) : filters.hidden;

  const shownColumns = table
    ? table.columns.filter((c) => !hidden.includes(c.key))
    : [];

  // Only the FX board has a grouped header. `table` is a union of the shapes
  // the category builders return, so the property is narrowed here once rather
  // than at the call site where the union erases it.
  const headerGroups =
    category === "rates" && table && "groups" in table
      ? (table.groups as { key: string; label: string; span: number }[])
      : undefined;

  // Hiding a bank has to shrink its heading too. A group row whose spans still
  // count dropped columns shifts every later heading left, so the board would
  // keep its labels and put them over the wrong prices — worse than showing
  // the column, because it looks correct.
  const shownGroups = headerGroups
    ? headerGroups
        .map((g) => ({
          ...g,
          span:
            g.span === 1
              ? shownColumns.some((c) => c.key === g.key)
                ? 1
                : 0
              : shownColumns.filter((c) => c.key.startsWith(`${g.key}__`)).length,
        }))
        .filter((g) => g.span > 0)
    : undefined;

  const rows = table
    ? sortRows(
        applyFilters(table.rows, table.columns, filters, "tr"),
        effectiveSort,
        table.columns,
        "tr",
      )
    : [];

  const run = () => {
    setSort(null);
    setFilters(EMPTY_FILTERS);
    if (category === "finance") {
      setQuery({
        kind: "finance",
        params: { family, amount: Number(amount), term: Number(term), banks: activeBanks },
      });
    } else if (category === "profit_share") {
      // Days, always. Five of the six banks answer in days natively, and
      // Albaraka returns ~10% less for "3 months" than for its own 92 days —
      // a run that mixed units would not be a comparison.
      setQuery({
        kind: "profit_share",
        params: {
          family, amount: Number(amount), term: Number(term),
          unit: "day", currency: effectiveCurrency, banks: activeBanks,
        },
      });
    } else if (category === "convert") {
      setQuery({
        kind: "convert",
        params: { source, target, amount: Number(amount), banks: activeBanks },
      });
    } else if (category === "card") {
      setQuery({
        kind: "card",
        bank: effectiveSingleBank,
        params: { card: effectiveCardCode, amount: Number(amount), installments: Number(installments) },
      });
    }
  };

  const unavailable = result.data?.unavailable ?? [];
  const ineligible = (banks ?? []).filter((b) => !b.publishes.includes(spec.capability));

  return (
    <VuiBox display="flex" flexDirection="column" gap="24px">
      <Card>
        <VuiBox mb="22px">
          <VuiTypography variant="lg" color="white">
            {t("title")}
          </VuiTypography>
        </VuiBox>

        {/* 1. what to compare */}
        <Dropdown
          label={t("whatToCompare")}
          value={category}
          options={(Object.keys(CATEGORIES) as CategoryKey[]).map((key) => ({
            value: key,
            label: t(`category.${key}`),
          }))}
          onChange={(next) => {
            setCategory(next as CategoryKey);
            setSelected(null);
            setSingleBank(null);
            setCardCode(null);
            setQuery(null);
            setSort(null);
          }}
        />

        {/* 2. which banks. Card and miles are priced per bank -- each bank
            sells its own cards, so there is no family to rank -- and get a
            single-select instead of the multi-bank picker the other four
            categories share. */}
        {/* The board's own filters replace the shared bank picker here: both
            were choosing banks, one by fetching and one by hiding columns, and
            two controls for one question is how you end up unable to tell
            which of them is in force. */}
        {category === "rates" ? (
          headerGroups && table && (
            <BoardFilters
              pairs={[...new Set(table.rows.map((r) => String(r.cells.instrument)))]}
              banks={headerGroups.filter((g) => g.span === 2).map((g) => g.key)}
              bankLabels={bankNames}
              state={filters}
              onChange={setFilters}
            />
          )
        ) : SINGLE_BANK_CATEGORIES.includes(category) ? (
          eligible.length > 0 && (
            <Dropdown
              label={t("bank")}
              value={effectiveSingleBank}
              options={eligible.map((b) => ({ value: b.name, label: b.display_name }))}
              onChange={(next) => { setSingleBank(next); setCardCode(null); setQuery(null); }}
            />
          )
        ) : (
          <Field label={t("banks")}>
            <BankPicker
              eligible={eligible}
              ineligible={ineligible}
              chosen={activeBanks}
              onChange={(next) => {
                setSelected(next);
                setQuery(null);
              }}
              allLabel={t("allBanks")}
              allSelectedLabel={t("allSelected")}
            />
          </Field>
        )}

        {/* 3. inputs, bounded by the selection */}
        {(category === "finance" || category === "profit_share") && (
          <Dropdown
            label={t("product")}
            value={family}
            options={families.map((f) => ({
              value: f.key,
              // The bank count belongs in the label, not a second line: a native
              // <option> renders one string, and knowing a family reaches two
              // banks rather than six is the whole basis for picking it.
              label: `${f.label} — ${t("banksOffering", { count: f.banks.length })}`,
            }))}
            onChange={(next) => { setFamily(next); setQuery(null); }}
          />
        )}

        {category === "convert" && (
          <VuiBox display="flex" gap="12px" flexWrap="wrap">
            <TextField label={t("from")} value={source} fullWidth={false}
              transform={(raw) => raw.toUpperCase()}
              onChange={(next) => { setSource(next); setQuery(null); }} />
            <TextField label={t("to")} value={target} fullWidth={false}
              transform={(raw) => raw.toUpperCase()}
              onChange={(next) => { setTarget(next); setQuery(null); }} />
          </VuiBox>
        )}

        {category === "profit_share" && (
          <Dropdown
            label={t("currency")}
            value={effectiveCurrency}
            options={currencyOptions.map((c) => ({ value: c, label: c }))}
            onChange={(next) => { setCurrency(next); setQuery(null); }}
          />
        )}

        {category === "card" && (
          <VuiBox display="flex" gap="12px" flexWrap="wrap" alignItems="flex-end">
            {cardCatalog && cardCatalog.length > 0 && (
              <Dropdown
                label={t("card")}
                value={effectiveCardCode}
                options={cardCatalog.map((c) => ({ value: c.code, label: c.name }))}
                onChange={(next) => { setCardCode(next); setQuery(null); }}
                minWidth="16rem"
                fullWidth={false}
              />
            )}
            <NumberField label={t("amount")} value={amount} fullWidth={false}
              onChange={(next) => { setAmount(next); setQuery(null); }} />
            <NumberField label={t("installments")} value={installments} fullWidth={false}
              onChange={(next) => { setInstallments(next); setQuery(null); }} />
            <ActionButton onClick={run} disabled={!effectiveSingleBank || !effectiveCardCode}>
              {t("run")}
            </ActionButton>
          </VuiBox>
        )}

        {category !== "rates" && category !== "miles" && category !== "card" && (
          <VuiBox display="flex" gap="12px" flexWrap="wrap" alignItems="flex-end">
            <NumberField label={t("amount")} value={amount} fullWidth={false}
              onChange={(next) => { setAmount(next); setQuery(null); }} />

            {category !== "convert" && (
              <NumberField
                label={category === "profit_share" ? t("termDays") : t("termMonths")}
                value={term}
                fullWidth={false}
                onChange={(next) => { setTerm(next); setQuery(null); }} />
            )}

            <ActionButton
              onClick={run}
              disabled={activeBanks.length === 0 || broken.size > 0}
            >
              {t("run")}
            </ActionButton>
          </VuiBox>
        )}

        {/* The ceiling, and who set it. */}
        {needsConstraints && bounds && (
          <VuiBox mt={2}>
            <Bounds
              bounds={bounds}
              banks={banks ?? []}
              t={t}
              broken={broken}
              unitLabel={category === "profit_share" ? t("unitDays") : t("unitMonths")}
              currency={category === "profit_share" ? effectiveCurrency : "₺"}
              locale={locale}
            />
          </VuiBox>
        )}
      </Card>

      {/* results */}
      {(table || result.isPending || cardQuoteResult.isPending) && (
        <Card>
          <VuiBox mb="22px" display="flex" alignItems="center" gap="12px" flexWrap="wrap">
            <VuiTypography variant="lg" color="white">{t("results")}</VuiTypography>
            {result.data?.seconds !== undefined && (
              <Pill tone="neutral">
                {t("took", {
                  seconds: result.data.seconds.toFixed(2),
                  count: activeBanks.length,
                })}
              </Pill>
            )}
          </VuiBox>

          {result.isError || cardQuoteResult.isError ? (
            <VuiTypography variant="button" color="text" fontWeight="regular">
              {tc("error")}
            </VuiTypography>
          ) : (
            table && (
              <>
                {category === "rates" && (
                  <VuiBox mb={2}>
                    {/* A board that has not moved since Friday looks broken
                        rather than closed, and the timestamps are the bank's
                        own words in six different formats, so this says it
                        once in plain language. */}
                    <Pill tone="warn">{t("weekendNotice")}</Pill>
                  </VuiBox>
                )}

              <ProducedTable
                columns={shownColumns}
                rows={rows}
                sort={effectiveSort}
                // Three states: ascending, descending, then off. The third
                // click is how a sort is cleared, and the heading now shows
                // which of them the next click gives.
                onSort={(key) =>
                  setSort((s) =>
                    s?.key === key
                      ? s.direction === "asc" ? { key, direction: "desc" } : null
                      : { key, direction: "asc" },
                  )
                }
                bankLabels={bankNames}
                emptyLabel={t("noResults")}
                movements={moved}
                best={category === "rates" && table && "best" in table
                  ? (table.best as Record<string, true>)
                  : undefined}
                rowKey={category === "rates" ? "instrument" : undefined}
                groups={shownGroups}
              />
              </>
            )
          )}
        </Card>
      )}

      {/* Always rendered once a run has happened, even when empty: the backend
          guarantees ranked + unavailable equals the banks in scope, and showing
          the arithmetic is what makes a short ranking trustworthy. */}
      {query !== null && result.data && (
        <Card>
          <VuiBox mb={2}>
            <VuiTypography variant="lg" color="white">{t("notRanked")}</VuiTypography>
          </VuiBox>
          {unavailable.length === 0 ? (
            <VuiTypography variant="button" color="text" fontWeight="regular">
              {t("allAnswered", { count: activeBanks.length })}
            </VuiTypography>
          ) : (
            <VuiBox display="flex" flexDirection="column" gap="10px">
              {unavailable.map((u) => (
                <VuiBox key={u.bank} display="flex" gap="10px" alignItems="flex-start" flexWrap="wrap">
                  <VuiTypography variant="button" color="white" fontWeight="medium">
                    {(banks ?? []).find((b) => b.name === u.bank)?.display_name ?? u.bank}
                  </VuiTypography>
                  <Pill tone={u.why === "not_offered" ? "neutral" : "warn"}>
                    {t(`why.${u.why}`)}
                  </Pill>
                  {u.detail && (
                    <VuiTypography variant="caption" color="text" sx={{ flex: "1 1 16rem" }}>
                      {u.detail}
                    </VuiTypography>
                  )}
                </VuiBox>
              ))}
            </VuiBox>
          )}
        </Card>
      )}
    </VuiBox>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <VuiBox mb={2}>
      <VuiBox mb={0.75}>
        <VuiTypography variant="caption" color="text">{label}</VuiTypography>
      </VuiBox>
      {children}
    </VuiBox>
  );
}

/** The bounds the selection allows, and the bank responsible for each. */
function Bounds({
  bounds,
  banks,
  t,
  unitLabel,
  currency,
  locale,
  broken,
}: {
  bounds: NonNullable<import("@/lib/api").Constraints["intersection"]>;
  banks: Bank[];
  t: ReturnType<typeof useTranslations<"comparator">>;
  /** "months" for financing, "days" for a participation account. */
  unitLabel: string;
  /** The currency the amounts are counted in — grams for a gold account. */
  currency: string;
  locale: string;
  /** Bound keys the current inputs violate; those pills turn from warn to bad. */
  broken: Set<string>;
}) {
  const name = (key: string) => banks.find((b) => b.name === key)?.display_name ?? key;
  const by = bounds.limited_by ?? {};

  // A participation term is counted in days and a gold balance in grams, so
  // neither "months" nor "₺" can be baked into the sentence: "up to 366 months"
  // and "up to 3.000 ₺" were both wrong on the participation page.
  const unit = unitLabel;
  const money = (value: number) => value.toLocaleString(locale === "tr" ? "tr-TR" : "en-US");

  const parts: [string, string][] = [];
  // Minimums first. They are what actually bites on a participation account --
  // Hayat will not open one below 50.000 ₺ and Kuveyt Türk's gold account
  // starts at 50 grams -- and a user who types a small amount otherwise learns
  // it only from a decline.
  if (bounds.min_amount != null) {
    parts.push([
      "min_amount",
      by.min_amount?.length
        ? t("minAmountBy", {
            amount: money(bounds.min_amount), currency,
            bank: by.min_amount.map(name).join(", "),
          })
        : t("minAmount", { amount: money(bounds.min_amount), currency }),
    ]);
  }
  if (bounds.max_amount != null) {
    parts.push([
      "max_amount",
      by.max_amount?.length
        ? t("maxAmountBy", {
            amount: money(bounds.max_amount), currency,
            bank: by.max_amount.map(name).join(", "),
          })
        : t("maxAmount", { amount: money(bounds.max_amount), currency }),
    ]);
  }
  if (bounds.min_term != null) {
    parts.push([
      "min_term",
      by.min_term?.length
        ? t("minTermBy", { term: bounds.min_term, unit, bank: by.min_term.map(name).join(", ") })
        : t("minTerm", { term: bounds.min_term, unit }),
    ]);
  }
  if (bounds.max_term != null) {
    parts.push([
      "max_term",
      by.max_term?.length
        ? t("maxTermBy", { term: bounds.max_term, unit, bank: by.max_term.map(name).join(", ") })
        : t("maxTerm", { term: bounds.max_term, unit }),
    ]);
  }
  if (parts.length === 0) return null;

  return (
    <VuiBox display="flex" flexWrap="wrap" gap="8px">
      {parts.map(([key, text]) => (
        <Pill key={key} tone={broken.has(key) ? "bad" : "warn"}>
          {text}
        </Pill>
      ))}
    </VuiBox>
  );
}

function BankPicker({
  eligible,
  ineligible,
  chosen,
  onChange,
  allLabel,
  allSelectedLabel,
}: {
  eligible: Bank[];
  ineligible: Bank[];
  chosen: string[];
  onChange: (next: string[]) => void;
  allLabel: string;
  allSelectedLabel: string;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const all = chosen.length === eligible.length;

  return (
    <>
      {/* Sits in the same column as the dropdowns, so it takes the same
          geometry rather than a button's own smaller one. */}
      <ActionButton
        variant="outlined"
        color="white"
        onClick={(e: MouseEvent<HTMLElement>) => setAnchor(e.currentTarget)}
      >
        {chosen.length === eligible.length
          ? allSelectedLabel
          : `${chosen.length} / ${eligible.length}`}
      </ActionButton>

      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { maxHeight: 360 } } }}>
        <MenuItem dense onClick={() => onChange(all ? [] : eligible.map((b) => b.name))}>
          <Checkbox checked={all} indeterminate={chosen.length > 0 && !all} size="small" sx={{ p: 0.5, mr: 1 }} />
          <VuiTypography variant="button" color="white" fontWeight="medium">{allLabel}</VuiTypography>
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />

        {eligible.map((b) => (
          <MenuItem key={b.name} dense
            onClick={() =>
              onChange(chosen.includes(b.name) ? chosen.filter((k) => k !== b.name) : [...chosen, b.name])
            }>
            <Checkbox checked={chosen.includes(b.name)} size="small" sx={{ p: 0.5, mr: 1 }} />
            <VuiTypography variant="button" color="white" fontWeight="regular">{b.display_name}</VuiTypography>
          </MenuItem>
        ))}

        {/* Shown, not hidden: a bank missing from the list looks like an
            omission, while a disabled one with its own note is an answer. */}
        {ineligible.length > 0 && <Divider sx={{ my: 0.5 }} />}
        {ineligible.map((b) => (
          <MenuItem key={b.name} dense disabled sx={{ opacity: 0.5 }}>
            <VuiBox sx={{ width: 30 }} />
            <VuiTypography variant="button" color="text" fontWeight="regular">
              {b.display_name}
            </VuiTypography>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
