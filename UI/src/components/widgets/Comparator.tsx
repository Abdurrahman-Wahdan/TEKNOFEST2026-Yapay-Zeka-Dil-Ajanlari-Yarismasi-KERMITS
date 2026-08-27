"use client";

import Card from "@mui/material/Card";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useMemo, useRef, useState } from "react";

import { ActionButton } from "@/components/ui/ActionButton";
import { AmountField, IntegerField } from "@/components/ui/AmountField";
import { CollapsibleCard } from "@/components/ui/CollapsibleCard";
import { Dropdown } from "@/components/ui/Dropdown";
import { MultiSelect } from "@/components/ui/MultiSelect";
import { Pill } from "@/components/ui/Pill";
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
import type { FilterState } from "@/lib/table-filter";
import { BANK_KEY, SIDE_KEY, hiddenColumns } from "@/lib/board-filter";
import { useRatesStream, type StreamedBoard } from "@/lib/use-rates-stream";
import { applyFilters, EMPTY_FILTERS, sortRows } from "@/lib/table-filter";
import { useBankLabels } from "@/lib/use-bank-labels";
import { useTableSort } from "@/lib/use-table-sort";

import { useAttachTable } from "@/lib/chat/use-attach-table";
import { useExportTable } from "@/lib/use-export-table";
import { LiveOverview } from "./LiveOverview";
import { ProducedTable } from "./ProducedTable";
import { BoardFilters } from "./BoardFilters";
import { TableFilters } from "./TableFilters";

/**
 * How often the FX board refreshes.
 *
 * The browser's interval, not the banks'. `api/cache.py` holds each bank's
 * board for a few seconds, so this can be short without the banks seeing it --
 * three seconds reads as live and still gives a cell that flashed time to be
 * noticed before the next poll lands.
 */
const RATES_POLL_MS = 3_000;

/**
 * How often the FX board's overview is rewritten.
 *
 * A hundred times the board's own poll, and that gap is the point. The prices
 * move every three seconds; what the model has to *say* about them does not,
 * and asking it every tick would be a 70-120 second vision call started twenty
 * times before the first one finished. Five minutes is slow enough to be
 * affordable and quick enough that a reader who has been watching the board
 * for a while is not reading a verdict on prices that have since moved.
 *
 * A tick over a board that has not actually changed is free: the server keys
 * the overview on the page outline, so an unmoved board -- a closed market, a
 * weekend -- serves the cache and never reaches the model.
 */
const OVERVIEW_REFRESH_MS = 5 * 60_000;

/** A submitted run. The tag picks the endpoint; `params` keeps its own types. */
type RunQuery =
  | { kind: "finance"; params: Parameters<typeof api.compareFinance>[0] }
  | { kind: "profit_share"; params: Parameters<typeof api.compareProfitShare>[0] }
  | { kind: "convert"; params: Parameters<typeof api.compareExchange>[0] }
  | { kind: "card"; params: Parameters<typeof api.compareCard>[0] };

/** Categories priced per bank rather than ranked across banks -- miles is
    reference data, one bank's whole reward table, nothing to compare. */
const SINGLE_BANK_CATEGORIES: CategoryKey[] = ["miles"];

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
  // Blank, not a figure nobody typed -- the field shows a "0" placeholder
  // (AmountField's default) until the user enters their own amount and term.
  const [amount, setAmount] = useState("");
  const [term, setTerm] = useState("");
  const [monthlyProfitRate, setMonthlyProfitRate] = useState("");
  const [currency, setCurrency] = useState("TRY");
  const [source, setSource] = useState("USD");
  const [target, setTarget] = useState("TRY");
  // Miles is priced per bank -- its whole reward table, not a comparison --
  // so it gets a dedicated single-bank selection, not the multi-bank picker
  // the ranked categories (including card, now) share below.
  const [singleBank, setSingleBank] = useState<string | null>(null);
  // Comparison families intentionally exclude offerings sold by only one bank.
  // Keep a separate bank picker for the complete live catalogue, otherwise a
  // product can be available through the API yet remain invisible in the UI.
  const [catalogueBank, setCatalogueBank] = useState("albaraka");
  const [installments, setInstallments] = useState("");
  // The toggle compares against raw `sort`, not `effectiveSort` below, so the
  // first click on a category's default-sorted column gives ascending rather
  // than descending. That is existing behaviour and deliberate to keep.
  const { sort, toggleSort, resetSort } = useTableSort();
  // Free text plus per-column tick-lists. The board is 32 rows across six
  // banks, so "where is the Qatari riyal" is a search, not a scroll.
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  // A discriminated union rather than a loose bag: the three comparison calls
  // take different parameters, and the tag is what lets the query function pick
  // the right one without casting away the types the client already has.
  const [query, setQuery] = useState<RunQuery | null>(null);

  const {
    data: banks,
    isPending: banksPending,
    isError: banksError,
  } = useQuery({ queryKey: ["banks"], queryFn: api.banks });
  const { data: familyList } = useQuery({ queryKey: ["families"], queryFn: api.families });

  const spec = CATEGORIES[category];
  // Keep these lookups explicit. `next-intl` can validate and hot-reload static
  // nested keys, while a runtime-built `familyGroup.${key}` can retain a stale
  // namespace during Turbopack updates and throw on an otherwise valid group.
  const familyGroupLabels: Record<string, string> = {
    personal: t("familyGroup.personal"),
    vehicle: t("familyGroup.vehicle"),
    property: t("familyGroup.property"),
    standard_account: t("familyGroup.standard_account"),
    special_account: t("familyGroup.special_account"),
  };


  /** Banks that can serve this category at all, and why the others cannot. */
  const eligible = useMemo(
    () => (banks ?? []).filter((b) => b.publishes.includes(spec.capability)),
    [banks, spec.capability],
  );
  const eligibleKeys = eligible.map((b) => b.name);
  const chosen = selected ?? eligibleKeys;
  const activeBanks = chosen.filter((b) => eligibleKeys.includes(b));
  const customRateBanks = (banks ?? []).filter(
    (bank) => activeBanks.includes(bank.name) && (bank.finance_input_capabilities ?? []).includes("monthly_profit_rate"),
  );
  const catalogueCategory = category === "profit_share" ? "profit_share" : "finance";
  const catalogueBanks = (banks ?? []).filter((bank) =>
    bank.publishes.includes(catalogueCategory),
  );
  const effectiveCatalogueBank = catalogueBanks.some((bank) => bank.name === catalogueBank)
    ? catalogueBank
    : (catalogueBanks[0]?.name ?? "");
  const { data: catalogueProducts, isLoading: catalogueLoading } = useQuery({
    queryKey: ["bankProducts", effectiveCatalogueBank, catalogueCategory],
    queryFn: () => api.bankProducts(effectiveCatalogueBank, catalogueCategory),
    enabled: (category === "finance" || category === "profit_share") && Boolean(effectiveCatalogueBank),
    staleTime: 15 * 60 * 1000,
  });
  // Which banks publish a rate feed is the registry's answer, not a list kept
  // here: a bank that starts publishing appears on the board without a release.
  const rateBanks = (banks ?? [])
    .filter((b) => b.publishes.includes("rates"))
    .map((b) => b.name)
    .filter((name) => activeBanks.includes(name));

  // The one bank a miles selection resolves to. Not derived from
  // `activeBanks`: those come from the multi-select picker used by the ranked
  // categories, and forcing miles through the same control would let someone
  // "select" two banks for a table that only ever comes from one.
  const effectiveSingleBank =
    singleBank && eligibleKeys.includes(singleBank) ? singleBank : (eligibleKeys[0] ?? "");

  // The mile-rate table is read-only reference data, like the rates board —
  // it loads on selecting a bank rather than waiting for a submit, because
  // there is nothing to submit: no amount, no term, just the published table.
  const {
    data: mileRates,
    isPending: mileRatesPending,
    isError: mileRatesError,
  } = useQuery({
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
      if (query.kind === "card") return api.compareCard(query.params);
      throw new Error("Unhandled comparison kind.");
    },
    enabled: query !== null,
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

  // What the converter's From/To fields may pick from -- every currency any
  // convert-eligible bank quotes, not one bank's list. A single bank's board
  // runs 14-27 rows and they do not agree with each other, so asking only
  // Kuveyt Türk would silently hide a currency Dünya converts. No polling: an
  // options list does not need to move every few seconds the way the live
  // board does, so this fetches once and sits on `staleTime` rather than
  // reusing the board's refetch interval.
  const currencyQueries = useQueries({
    queries: rateBanks.map((bank) => ({
      queryKey: ["rates", bank],
      queryFn: () => api.bankRates(bank),
      enabled: category === "convert",
      staleTime: RATES_POLL_MS,
    })),
  });
  const convertCurrencyOptions = useMemo(() => {
    if (category !== "convert") return [];
    const codes = new Set<string>(["TRY"]);
    for (const q of currencyQueries) {
      for (const r of q.data ?? []) {
        // A cross pair (EUR/USD) is not a single currency the field can send
        // as `source` or `target`.
        if (!r.code.includes("/")) codes.add(r.canonical || r.code);
      }
    }
    return [...codes].sort((a, b) => (a === "TRY" ? -1 : b === "TRY" ? 1 : a.localeCompare(b)));
  }, [category, currencyQueries]);

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

  // Same reasoning as `effectiveCurrency`: the options list arrives after the
  // first render (it is a fetch), so the state a user picked before it loaded
  // -- or the "USD"/"TRY" defaults, before anything has loaded at all -- has
  // to be checked against what is actually offered rather than sent as-is.
  const effectiveSource = convertCurrencyOptions.includes(source)
    ? source
    : (convertCurrencyOptions[0] ?? source);
  const effectiveTarget = convertCurrencyOptions.includes(target)
    ? target
    : (convertCurrencyOptions.find((c) => c !== effectiveSource) ?? target);

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

  // The amount and term/instalment fields start blank -- a "0" placeholder,
  // not a figure nobody typed -- so Compare has to stay off until there is a
  // real number in each one the category actually asks for, rather than
  // quietly sending 0 and getting a decline from every bank.
  const missingInput =
    !(Number(amount) > 0) ||
    (category === "card"
      ? !(Number(installments) > 0)
      : category !== "convert" && !(Number(term) > 0));

  // The labels only. The `banks` query above stays: this component needs the
  // bank *records* for eligibility and the rate board, not just display names.
  // Both hooks share one `["banks"]` cache entry, so it is still one request.
  const bankNames = useBankLabels();

  /**
   * What this table is, and what was asked to produce it.
   *
   * An instalment figure is meaningless without the amount and term behind it, so
   * a quoted cell that travels without them forces the agent to ask the user what
   * they typed -- which is the follow-up question attaching the row was supposed
   * to remove. Read from `query`, the parameters actually submitted, not from the
   * live form state: the fields can be edited after Compare was pressed, and the
   * table on screen still belongs to the old query.
   *
   * Bank keys are swapped for the names on screen, for the same reason a `bank`
   * cell is: `kuveytturk` is not what the user is looking at.
   */
  const tableAbout = useMemo(() => {
    if (!query) return undefined;
    const params = Object.entries(query.params)
      .filter(([, value]) => value !== undefined && value !== null && value !== "")
      .map(([key, value]) => {
        const text = Array.isArray(value)
          ? value.map((v) => bankNames[String(v)] ?? String(v)).join(", ")
          : String(value);
        return `${key}=${text}`;
      })
      .join("; ");
    return params || undefined;
  }, [query, bankNames]);


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
    unit: t("unit"), perUnit: t("perUnit"), perGram: t("perGram"), perCoin: t("perCoin"),
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
            if (rateGroup(r) === "parity" || (r.quote_currency ?? "TRY") !== "TRY") continue;
            collected.push({ ...r, bank });
          }
        }
      } else {
        rateQueries.forEach((q, i) => {
          for (const r of (q.data ?? []) as Rate[]) {
            if (rateGroup(r) === "parity" || (r.quote_currency ?? "TRY") !== "TRY") continue; // not a TRY price
            collected.push({ ...r, bank: rateBanks[i] });
          }
        });
      }
      const live = [...new Set(collected.map((r) => r.bank))];
      return ratesBoard(collected, live.length ? live : rateBanks, labels, bankNames);
    }
    if (category === "miles") {
      if (!mileRates) return null;
      return mileRatesTable(mileRates, labels);
    }
    const c = result.data;
    if (!c) return null;
    if (category === "finance") return financeTable(c, labels);
    if (category === "profit_share") return profitShareTable(c, labels);
    if (category === "convert") return convertTable(c, labels);
    if (category === "card") return cardTable(c, labels);
    return null;
  })();

  // Each category owns its loading state. The submit-driven query is disabled
  // for the two read-only boards, and TanStack correctly keeps such a query in
  // `pending` because it has no data. Letting that unrelated state control the
  // Results card is what left FX Rates saying "Loading..." after its stream and
  // REST fallbacks had already returned.
  const ratesHaveData = stream.live || rateQueries.some((q) => (q.data?.length ?? 0) > 0);
  const ratesPending = category === "rates"
    && !ratesHaveData
    && (banksPending || rateQueries.some((q) => q.isPending || q.isFetching));
  const ratesError = category === "rates"
    && !ratesHaveData
    && (banksError || (rateQueries.length > 0 && rateQueries.every((q) => q.isError)));
  const submitDriven = category !== "rates" && category !== "miles";
  const resultsPending = category === "rates"
    ? ratesPending
    : category === "miles"
      ? mileRatesPending
      : submitDriven && query !== null && result.isPending;
  const resultsError = category === "rates"
    ? ratesError
    : category === "miles"
      ? mileRatesError
      : submitDriven && query !== null && result.isError;
  const resultsVisible = category === "rates" || category === "miles"
    ? resultsPending || resultsError || table !== null
    : query !== null;

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

  // `__bank` and `__side` are reserved keys `hiddenColumns` reads to decide
  // which *columns* to drop -- they steer `BoardFilters` above, not a real
  // column any row has a cell for. `applyFilters` cannot tell a reserved key
  // from a real one, so a selection there does not mean "no filter" the way
  // an empty selection on a real column does; every row's `cellText(undefined)`
  // fails to match every ticked bank name, and the board goes blank the
  // moment a single bank or side is deselected. They drive `hiddenColumns`
  // above and stop there.
  const rowFilters =
    category === "rates"
      ? { ...filters, values: { ...filters.values, [BANK_KEY]: [], [SIDE_KEY]: [] } }
      : filters;
  const rows = table
    ? sortRows(
        applyFilters(table.rows, table.columns, rowFilters, "tr"),
        effectiveSort,
        table.columns,
        "tr",
        bankNames,
      )
    : [];
  const tableTitle = t(`category.${category}`);

  // The filtered, sorted, visible rows -- what the user is actually looking at.
  const attach = useAttachTable({
    columns: shownColumns,
    rows,
    title: tableTitle,
    about: tableAbout,
    bankLabels: bankNames,
    groups: shownGroups,
  });

  // The board's "full table" is every column including the banks and sides the
  // user unticked, and every row before the filters ran -- which on `/compare`
  // is a genuinely different document from what is on screen.
  const exporter = useExportTable({
    view: { columns: shownColumns, rows },
    full: { columns: table?.columns ?? [], rows: table?.rows ?? [] },
    title: tableTitle,
    subtitle: tableAbout,
    bankLabels: bankNames,
  });

  const run = () => {
    resetSort();
    setFilters(EMPTY_FILTERS);
    if (category === "finance") {
      setQuery({
        kind: "finance",
        params: {
          family,
          amount: Number(amount),
          term: Number(term),
          banks: activeBanks,
          monthly_profit_rate: Number(monthlyProfitRate) > 0 ? Number(monthlyProfitRate) : undefined,
        },
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
        params: {
          source: effectiveSource, target: effectiveTarget,
          amount: Number(amount), banks: activeBanks,
        },
      });
    } else if (category === "card") {
      setQuery({
        kind: "card",
        params: {
          amount: Number(amount), installments: Number(installments), banks: activeBanks,
        },
      });
    }
  };

  const unavailable = result.data?.unavailable ?? [];
  const ineligible = (banks ?? []).filter((b) => !b.publishes.includes(spec.capability));
  const resultsRef = useRef<HTMLDivElement>(null);

  // The five-minute tick the FX overview rewrites itself on. A counter rather
  // than a reading of the clock, so the first overview is written when the
  // board arrives and the next one five minutes after *that*, instead of at
  // whatever phase of the hour the tab happened to open at.
  const [boardTick, setBoardTick] = useState(0);
  useEffect(() => {
    // Only the rates board moves on its own; every other category changes
    // because the user did something, and an interval running under them would
    // rewrite a finished comparison nobody had touched.
    if (category !== "rates") return;
    const id = setInterval(() => setBoardTick((n) => n + 1), OVERVIEW_REFRESH_MS);
    return () => clearInterval(id);
  }, [category]);

  /**
   * When what is on screen has become a different thing, and so wants reading
   * again. `LiveOverview` regenerates on every change of this and on nothing
   * else, so the three cadences the page actually has are stated here.
   *
   *  - **The FX board rewrites itself on a timer.** It is the one thing here
   *    that moves without anybody asking, so the tick is the revision. A tick
   *    over a board that has not moved is free — the server keys the overview
   *    on the page outline, so a closed market serves the cache.
   *  - **Miles is read once.** It is one bank's published reward table, not a
   *    comparison and not live; the bank is the only thing that can make it a
   *    different table.
   *  - **Everything else waits for Compare, and for the answer.** Keyed on the
   *    submitted parameters *and* on when the result landed, which is what
   *    makes the card spin from the press and generate from the arrival:
   *    pressing Compare changes the parameters, so the previous verdict is
   *    dropped immediately, and `ready` below holds the generation back until
   *    the rows are actually on screen. Re-running the identical comparison
   *    changes neither, which is correct — the same question over the same
   *    answer has the same overview.
   */
  const overviewRevision =
    category === "rates"
      ? `rates:${boardTick}`
      : category === "miles"
        ? `miles:${effectiveSingleBank}`
        : `${category}:${JSON.stringify(query)}:${result.dataUpdatedAt}`;

  // Read the page only once there is a table drawn in it, with rows. The
  // overview is written from the page outline, so reading during a load hands
  // the model a spinner and asks it what the comparison shows -- and reading a
  // board that came back empty asks it to rank nothing.
  const overviewReady =
    (table?.rows.length ?? 0) > 0 && !resultsPending && !resultsError;

  // The Results card mounts on the first Compare, so on a tall form it can
  // appear below the fold and a successful click reads as doing nothing.
  useEffect(() => {
    if (query !== null && category !== "rates" && category !== "miles") {
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [query, category]);

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
            const nextCategory = next as CategoryKey;
            setCategory(nextCategory);
            // "konut-yeni" is a finance family and does not exist under
            // profit_share -- left as-is, switching to Participation account
            // and pressing Compare without touching the Product field sent
            // that stale key straight to /compare/profit-share and every bank
            // came back a 422. The two categories' pickers share this one
            // piece of state, so switching between them has to reset it to a
            // family the new category actually has.
            setFamily(nextCategory === "profit_share" ? "katilma" : "konut-yeni");
            setSelected(null);
            setSingleBank(null);
            setQuery(null);
            resetSort();
            // Rates and miles load without a Compare press, so a filter set
            // on one and carried into the other would hide rows for a reason
            // the new table gives no sign of -- run() already clears this for
            // every category that goes through a submit.
            setFilters(EMPTY_FILTERS);
          }}
        />

        {/* 2. which banks. Miles is priced per bank -- one bank's whole reward
            table, nothing to rank -- and gets a single-select instead of the
            multi-bank picker every other category shares, card included. */}
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
            <>
              <Dropdown
                label={t("bank")}
                value={effectiveSingleBank}
                options={eligible.map((b) => ({ value: b.name, label: b.display_name }))}
                onChange={(next) => { setSingleBank(next); setQuery(null); }}
              />
              {/* Same component and same call as the FX board's filters, and
                  the same spot -- directly under the bank dropdown, not down
                  in the Results card where it used to sit, disconnected from
                  the control it filters. */}
              {table && (
                <VuiBox mt={2}>
                  <TableFilters
                    columns={table.columns}
                    rows={table.rows}
                    state={filters}
                    onChange={setFilters}
                    matched={rows.length}
                    total={table.rows.length}
                  />
                </VuiBox>
              )}
            </>
          )
        ) : (
          <VuiBox mb={2}>
            <MultiSelect
              label={t("banks")}
              options={eligible.map((b) => ({ value: b.name, label: b.display_name }))}
              disabledOptions={ineligible.map((b) => ({ value: b.name, label: b.display_name }))}
              selected={activeBanks}
              onChange={(next) => {
                setSelected(next);
                setQuery(null);
              }}
              allLabel={t("allBanks")}
              allSelectedLabel={t("allSelected")}
            />
          </VuiBox>
        )}

        {/* 3. inputs, bounded by the selection */}
        {(category === "finance" || category === "profit_share") && (
          <Dropdown
            label={t("product")}
            value={family}
            options={families.map((f) => ({
              value: f.key,
              group: familyGroupLabels[f.group] ?? f.group,
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
            <Dropdown
              label={t("from")}
              value={effectiveSource}
              options={convertCurrencyOptions.map((c) => ({ value: c, label: c }))}
              onChange={(next) => { setSource(next); setQuery(null); }}
              fullWidth={false}
            />
            <Dropdown
              label={t("to")}
              value={effectiveTarget}
              options={convertCurrencyOptions.map((c) => ({ value: c, label: c }))}
              onChange={(next) => { setTarget(next); setQuery(null); }}
              fullWidth={false}
            />
          </VuiBox>
        )}

        {category === "profit_share" && currencyOptions.length > 1 && (
          <Dropdown
            label={t("currency")}
            value={effectiveCurrency}
            options={currencyOptions.map((c) => ({ value: c, label: c }))}
            onChange={(next) => { setCurrency(next); setQuery(null); }}
          />
        )}

        {category !== "rates" && category !== "miles" && (
          <VuiBox display="flex" gap="12px" flexWrap="wrap" alignItems="flex-end">
            <AmountField label={t("amount")} value={amount} fullWidth={false}
              onChange={(next) => { setAmount(next); setQuery(null); }} />

            {category === "card" ? (
              <IntegerField label={t("installments")} value={installments} fullWidth={false}
                onChange={(next) => { setInstallments(next); setQuery(null); }} />
            ) : category !== "convert" && (
              <IntegerField
                label={category === "profit_share" ? t("termDays") : t("termMonths")}
                value={term}
                fullWidth={false}
                onChange={(next) => { setTerm(next); setQuery(null); }} />
            )}

            {category === "finance" && (
              <AmountField
                label={t("customProfitRate")}
                value={monthlyProfitRate}
                fullWidth={false}
                minWidth="12rem"
                onChange={(next) => { setMonthlyProfitRate(next); setQuery(null); }}
              />
            )}

            <ActionButton
              onClick={run}
              disabled={activeBanks.length === 0 || broken.size > 0 || missingInput}
            >
              {t("run")}
            </ActionButton>
          </VuiBox>
        )}

        {category === "finance" && Number(monthlyProfitRate) > 0 && (
          <VuiTypography variant="caption" color="text" sx={{ display: "block", mt: 1 }}>
            {t("customProfitRateHint", {
              banks: customRateBanks.map((bank) => bank.display_name).join(", ") || t("noResults"),
            })}
          </VuiTypography>
        )}

        {/* The ceiling, and who set it -- only once the current input actually
            breaks one. A rule nobody has broken is not information the form
            needs to spend space on; it is only worth a pill the moment 120
            months meets a bank whose maximum is 84. */}
        {needsConstraints && bounds && broken.size > 0 && (
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

      {/* results -- mounted only once there is something to show: the rates
          board and the mile table load on their own, everything else waits
          for the user to press Compare. Without this, an empty "Results" card
          sat under the form on every category before a single request had
          been made. */}
      {/* Above the results, and inside the same gate: before the first Compare
          there is no comparison to read, so on the submit-driven categories
          this is not on screen at all rather than sitting empty. The rates
          board and the mile table open the gate themselves, which is why those
          two get an overview without anybody pressing anything. */}
      {resultsVisible && (
        <LiveOverview ready={overviewReady} revision={overviewRevision} />
      )}

      {resultsVisible && (
        <Card ref={resultsRef}>
          <VuiBox mb="22px" display="flex" alignItems="center" gap="12px" flexWrap="wrap">
            <VuiTypography variant="lg" color="white">{t("results")}</VuiTypography>
            {/* Unmounted, not deleted: how long the round trip took is a
                debugging fact, not something the end user needs to see on
                every category -- the same call that dropped it for convert.
                `result.data.seconds` still arrives on the wire and `t("took")`
                is still a live translation; bringing the pill back is one
                block, not a redesign. */}
          </VuiBox>

          {query?.kind === "finance" && query.params.monthly_profit_rate !== undefined && (
            <VuiBox mb={2}>
              <Pill tone="warn">
                {t("customProfitRateResult", { rate: query.params.monthly_profit_rate })}
              </Pill>
            </VuiBox>
          )}

          {resultsPending ? (
            <VuiTypography variant="button" color="text" fontWeight="regular">
              {tc("loading")}
            </VuiTypography>
          ) : resultsError ? (
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
                onSort={toggleSort}
                bankLabels={bankNames}
                emptyLabel={t("noResults")}
                movements={moved}
                best={category === "rates" && table && "best" in table
                  ? (table.best as Record<string, true>)
                  : undefined}
                rowKey={category === "rates" ? "instrument" : undefined}
                groups={shownGroups}
                // Not drawn: the card header already names the comparison. This
                // is so a quoted cell can say which comparison it came out of.
                title={tableTitle}
                about={tableAbout}
                onAttachRow={attach.onAttachRow}
                onAttachTable={attach.onAttachTable}
                onExportTable={exporter.onExportTable}
              />
              {exporter.dialog}
              </>
            )
          )}
        </Card>
      )}

      {/* Unmounted, not merely empty, once every active bank answered: a card
          that only ever says "all N banks answered" is not information, it is
          confirmation of the default case, on every single run. Shown only
          when there is an actual gap to explain -- same rule anywhere else in
          the app that lists what did not come back. */}
      {query !== null && result.data && unavailable.length > 0 && (
        <Card>
          <VuiBox mb={2}>
            <VuiTypography variant="lg" color="white">{t("notRanked")}</VuiTypography>
          </VuiBox>
          <VuiBox display="flex" flexDirection="column" gap="10px">
            {unavailable.map((u, i) => (
              // Not `u.bank` alone: card comparisons ask per card, not per
              // bank, so one bank can decline several times over -- Kuveyt
              // Türk's five cards each have their own instalment ceiling, and
              // each refusal is its own row with its own detail sentence.
              <VuiBox key={`${u.bank}-${i}`} display="flex" gap="10px" alignItems="flex-start" flexWrap="wrap">
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
        </Card>
      )}

      {/* Last on the page, below the ranking and below the banks that could
          not be ranked: this is deliberately separate from the
          comparison-family picker above. A product does not disappear merely
          because no second bank sells the same thing -- the catalogue is the
          complete set of bank-published options, while the picker remains the
          subset that can be ranked. */}
      {(category === "finance" || category === "profit_share") && (
        // Folded on arrival: it is the longest card on the page and the one
        // nobody came for -- the ranking above answers the question, this
        // answers "what else does that bank sell". Its description stays
        // visible while folded, so the heading still says what is inside.
        <CollapsibleCard
          title={t("catalogueTitle")}
          description={t("catalogueDescription")}
          defaultCollapsed
        >
          <VuiBox mt={2}>
            <Dropdown
              label={t("bank")}
              value={effectiveCatalogueBank}
              options={catalogueBanks.map((bank) => ({ value: bank.name, label: bank.display_name }))}
              onChange={setCatalogueBank}
            />
          </VuiBox>
          <VuiBox mt={2} display="flex" flexDirection="column" gap="8px">
            {catalogueLoading ? (
              <VuiTypography variant="caption" color="text">{tc("loading")}</VuiTypography>
            ) : catalogueProducts?.length ? (
              catalogueProducts.map((product) => (
                <VuiBox
                  key={product.code}
                  display="flex"
                  alignItems="center"
                  justifyContent="space-between"
                  gap="12px"
                  flexWrap="wrap"
                  sx={{
                    borderBottom: "1px solid",
                    borderColor: "borders.main",
                    paddingBottom: "8px",
                  }}
                  >
                    <VuiTypography variant="button" color="white" fontWeight="medium">
                      {product.name}
                    </VuiTypography>
                </VuiBox>
              ))
            ) : (
              <VuiTypography variant="caption" color="text">{t("noResults")}</VuiTypography>
            )}
          </VuiBox>
        </CollapsibleCard>
      )}
    </VuiBox>
  );
}


/** The bounds the current input actually breaks, and the bank responsible.
    Only ever renders a violation -- a limit nobody has hit is not shown. */
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
  // Everything above is a fact about the selection, worth knowing at any time;
  // only a fact the current amount or term actually contradicts is worth a
  // pill. Filtered here rather than left to the caller, so every place this
  // component is used gets the same "errors only" behaviour for free.
  const violated = parts.filter(([key]) => broken.has(key));
  if (violated.length === 0) return null;

  return (
    <VuiBox display="flex" flexWrap="wrap" gap="8px">
      {violated.map(([key, text]) => (
        <Pill key={key} tone="bad">
          {text}
        </Pill>
      ))}
    </VuiBox>
  );
}
