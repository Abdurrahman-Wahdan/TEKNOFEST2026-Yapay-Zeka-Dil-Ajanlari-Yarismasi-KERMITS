import type { ComponentPropsWithoutRef } from "react";

/**
 * Props for a markdown element override.
 *
 * Streamdown hands every overridden element the parsed HAST `node` alongside the
 * usual HTML attributes. Useful for inspecting the source markdown, meaningless
 * to the DOM.
 */
export type El<T extends keyof React.JSX.IntrinsicElements> =
  ComponentPropsWithoutRef<T> & { node?: unknown };

/**
 * The props minus `node`, ready to spread onto a real element.
 *
 * Spreading the props through untouched puts `node="[object Object]"` on the
 * rendered tag and earns a React warning for every heading and cell in the
 * answer. Written as a copy-and-delete rather than a destructure so there is no
 * discarded binding for the linter to complain about.
 */
export function domProps<P extends { node?: unknown }>(props: P): Omit<P, "node"> {
  const rest = { ...props } as Record<string, unknown>;
  delete rest.node;
  return rest as Omit<P, "node">;
}
