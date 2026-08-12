import type { ButtonHTMLAttributes } from "react";

import styles from "./Button.module.scss";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
};

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled,
  children,
  className,
  ...rest
}: Props) {
  return (
    <button
      // type defaults to "submit" in HTML, so a plain button inside a form
      // submits it. That is almost never what the caller meant.
      type="button"
      className={[styles.button, className].filter(Boolean).join(" ")}
      data-variant={variant}
      data-size={size}
      disabled={disabled || loading}
      // Announces the pending state to a screen reader, which a spinner alone
      // does not.
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <span className={styles.spinner} aria-hidden="true" />}
      {children}
    </button>
  );
}
