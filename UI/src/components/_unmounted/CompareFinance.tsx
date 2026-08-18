"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "./Button";
import { Card, CardGrid } from "@/components/ui/Card";
import { api, type Unavailable } from "@/lib/api";
import { formatMoney, formatRate } from "@/lib/format";

import styles from "./CompareFinance.module.scss";

type Query = { family: string; amount: number; term: number };

/**
 * One financing product at every bank that sells it.
 *
 * The comparison is `enabled: false` until the user presses the button. Each
 * run is a live fan-out to ten banks' own calculators, and firing it on every
 * keystroke would both be slow and look like a burst to a WAF — which is
 * exactly what gets an address throttled.
 */
export function CompareFinance() {
  const t = useTranslations("compare");
  const tc = useTranslations("common");
  const locale = useLocale() as "tr" | "en";

  const [form, setForm] = useState<Query>({
    family: "konut-yeni",
    amount: 1_000_000,
    term: 120,
  });
  const [query, setQuery] = useState<Query | null>(null);

  const families = useQuery({ queryKey: ["families"], queryFn: api.families });

  const comparison = useQuery({
    queryKey: ["compare", "finance", query],
    queryFn: () => api.compareFinance(query!),
    enabled: query !== null,
  });

  const financeFamilies = (families.data ?? []).filter(
    (f) => f.category === "finance",
  );

  // The API always sends these arrays, but they carry a default on the Python
  // side, so the generated schema marks them optional. Defaulted once here
  // rather than guarded at each of the four places they are read.
  const quotes = comparison.data?.quotes ?? [];
  const unavailable = comparison.data?.unavailable ?? [];

  return (
    <CardGrid>
      <Card span={4}>
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(form);
          }}
        >
          <label className={styles.field}>
            <span>{t("family")}</span>
            <select
              value={form.family}
              onChange={(e) => setForm({ ...form, family: e.target.value })}
            >
              {financeFamilies.map((family) => (
                <option key={family.key} value={family.key}>
                  {family.label} ({family.banks.length})
                </option>
              ))}
            </select>
          </label>

          <label className={styles.field}>
            <span>{t("amount")}</span>
            <input
              type="number"
              min={1}
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: Number(e.target.value) })}
            />
          </label>

          <label className={styles.field}>
            <span>{t("term")}</span>
            <input
              type="number"
              min={1}
              max={360}
              value={form.term}
              onChange={(e) => setForm({ ...form, term: Number(e.target.value) })}
            />
          </label>

          <Button type="submit" variant="primary" loading={comparison.isFetching}>
            {t("run")}
          </Button>
        </form>
      </Card>

      {comparison.data && (
        <>
          <Card
            span={4}
            subtitle={t("tookSeconds", {
              count: quotes.length,
              seconds: comparison.data.seconds.toFixed(1),
            })}
          >
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th scope="col">{t("bank")}</th>
                    <th scope="col">{t("installment")}</th>
                    <th scope="col">{t("total")}</th>
                    <th scope="col">{t("profitRate")}</th>
                    <th scope="col">{t("annualCost")}</th>
                  </tr>
                </thead>
                <tbody>
                  {/* Cheapest instalment first. The API returns the banks in
                      whatever order they answered, which is arrival order, not
                      an answer to "which is best". */}
                  {/* A bank can publish a rate and no payment at all (Türkiye
                      Finans works its instalments out in the browser), so a
                      missing figure sinks to the bottom instead of sorting as
                      zero and being crowned the cheapest. */}
                  {[...quotes]
                    .sort((a, b) =>
                      (a.installment ?? Infinity) - (b.installment ?? Infinity))
                    .map((quote) => (
                      <tr key={`${quote.bank}-${quote.variant}`}>
                        <th scope="row">{quote.bank}</th>
                        <td>
                          {quote.installment == null
                            ? "—"
                            : formatMoney(quote.installment, locale)}
                        </td>
                        <td>
                          {quote.total == null
                            ? "—"
                            : formatMoney(quote.total, locale)}
                        </td>
                        <td>{formatRate(quote.profit_rate, locale)}</td>
                        <td>
                          {/* A bank that publishes no annual cost rate shows a
                              dash. Nullish, not `=== null`: the field is
                              optional in the schema, so it can also be absent. */}
                          {quote.annual_cost_rate == null
                            ? "—"
                            : formatRate(quote.annual_cost_rate, locale)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </Card>

          {unavailable.length > 0 && (
            <Card span={4} title={t("unavailable")}>
              <ul className={styles.unavailable}>
                {unavailable.map((entry: Unavailable) => (
                  <li key={`${entry.bank}-${entry.why}`}>
                    <strong>{entry.bank}</strong>{" "}
                    <span data-why={entry.why}>
                      {/* Four distinct reasons, shown as four distinct
                          sentences. "Does not sell this" and "did not answer"
                          must never read the same. */}
                      {t(`why.${entry.why}` as "why.not_offered")}
                    </span>
                    {entry.detail && (
                      <span className={styles.detail}> — {entry.detail}</span>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}

      {comparison.isError && (
        <Card span={4}>
          <p>{tc("error")}</p>
        </Card>
      )}
    </CardGrid>
  );
}
