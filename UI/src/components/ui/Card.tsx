import type { ReactNode } from "react";

import styles from "./Card.module.scss";

/**
 * The surface every dashboard component sits on.
 *
 * Every widget in the catalog renders inside one of these, which is what makes
 * an AI-composed layout look composed rather than assembled — the tiles share a
 * frame whoever chose them.
 */
export function Card({
  title,
  subtitle,
  actions,
  children,
  span = 1,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** Grid columns to occupy, 1–4. Clamped by the grid on narrow screens. */
  span?: 1 | 2 | 3 | 4;
}) {
  return (
    <section className={styles.card} data-span={span}>
      {(title || actions) && (
        <header className={styles.header}>
          <div>
            {title && <h2 className={styles.title}>{title}</h2>}
            {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
          </div>
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      <div className={styles.body}>{children}</div>
    </section>
  );
}

/** The grid cards are laid out in. */
export function CardGrid({ children }: { children: ReactNode }) {
  return <div className={styles.grid}>{children}</div>;
}
