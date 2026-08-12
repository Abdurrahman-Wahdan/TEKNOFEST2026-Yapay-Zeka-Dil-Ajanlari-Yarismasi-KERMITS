"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import { api } from "@/lib/api";

import styles from "./BankRegistry.module.scss";

/**
 * Every bank, what it publishes, and what is currently down.
 *
 * The three states are shown as three different things, because they are: a
 * bank that does not publish an endpoint (a permanent, legitimate answer with a
 * reason), one whose endpoint failed this morning (temporary), and one that is
 * fine. Collapsing them into "unavailable" would tell a user to wait for
 * something that is never coming.
 */
export function BankRegistry() {
  const t = useTranslations("banks");
  const tc = useTranslations("common");
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ["banks"],
    queryFn: api.banks,
  });

  if (isPending) return <p className={styles.muted}>{tc("loading")}</p>;
  if (isError) {
    return (
      <p className={styles.muted}>
        {tc("error")}{" "}
        <button className={styles.retry} onClick={() => refetch()}>
          {tc("retry")}
        </button>
      </p>
    );
  }

  return (
    <ul className={styles.list}>
      {data.map((bank) => (
        <li key={bank.name} className={styles.row}>
          <Link href={`/banks/${bank.name}`} className={styles.name}>
            {bank.display_name}
          </Link>

          <div className={styles.tags}>
            {bank.publishes.length === 0 && (
              <span className={styles.tag} data-tone="neutral">
                {t("noEndpoints")}
              </span>
            )}
            {bank.publishes.map((capability) => (
              <span
                key={capability}
                className={styles.tag}
                // A capability that is published but currently failing is
                // marked rather than hidden — hiding it would look identical
                // to a bank that never offered it.
                data-tone={
                  (bank.maintenance ?? []).includes(capability) ? "warn" : "ok"
                }
              >
                {capability}
              </span>
            ))}
          </div>

          {bank.notes && <p className={styles.notes}>{bank.notes}</p>}
        </li>
      ))}
    </ul>
  );
}
