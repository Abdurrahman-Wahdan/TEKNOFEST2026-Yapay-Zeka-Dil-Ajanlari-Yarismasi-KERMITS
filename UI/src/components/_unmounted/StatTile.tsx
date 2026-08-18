import type { ReactNode } from "react";

import styles from "./StatTile.module.scss";

/**
 * One number and what it means.
 *
 * The value is `numeric` (tabular figures) because these sit in rows and
 * proportional digits make a row of them visibly ragged.
 */
export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "danger";
}) {
  return (
    <div className={styles.tile} data-tone={tone}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>{value}</span>
      {hint && <span className={styles.hint}>{hint}</span>}
    </div>
  );
}
