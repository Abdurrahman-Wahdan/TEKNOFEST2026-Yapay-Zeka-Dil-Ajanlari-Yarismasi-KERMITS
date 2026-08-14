import type { ComponentType } from "react";
import type { ZodTypeAny } from "zod";

import { TablePropsSchema } from "@/lib/contract";
import { FALLBACK_RULE, type SpanRule } from "@/lib/layout";

import { BankRegistry } from "./BankRegistry";
import { TableWidget } from "./TableWidget";

/**
 * The component catalog — everything a producer is allowed to ask for.
 *
 * A produced page is a list of `{type, props}`, where `type` is a key in here,
 * so the model picks from a fixed, known set rather than emitting layout code.
 * Everything it can name is therefore something that renders.
 *
 * Each entry carries three things beyond the component itself:
 *
 *  - `props` — a schema. The API stores `props` unvalidated on purpose (the
 *    schemas live here in TypeScript, and duplicating them in Python would
 *    guarantee drift), which makes this the only validation in the system.
 *  - `span` — how wide it wants to be. The *producer* never sets this; the
 *    layout engine reads it. See `@/lib/layout`.
 *  - nothing about titles. Card headings come from the component or from our
 *    own i18n, never from a key the model chose — next-intl throws on a
 *    missing key, so one hallucinated key would blank the page.
 *
 * Adding a widget: build it so it renders entirely from its props, write its
 * schema, register it below. Nothing else changes — not the renderer, not the
 * layout engine, not any page.
 */
export interface WidgetSpec {
  component: ComponentType<never>;
  props: ZodTypeAny;
  span: SpanRule;
}

export const CATALOG: Record<string, WidgetSpec> = {
  /**
   * The workhorse. A table's whole shape — column count, labels, row count —
   * comes from the data, so one entry here covers every table any producer
   * will ever send.
   */
  table: {
    component: TableWidget as ComponentType<never>,
    props: TablePropsSchema,
    // Tables want the full width and are still readable at half.
    span: { preferred: 4, min: 2 },
  },

  /**
   * Live registry data rather than corpus content — kept in the catalog
   * because a produced page may legitimately want to sit a table next to
   * "which banks publish what". It takes no props.
   */
  BankRegistry: {
    component: BankRegistry as ComponentType<never>,
    props: TablePropsSchema.partial().optional(),
    span: { preferred: 2, min: 2 },
  },
};

export type WidgetType = keyof typeof CATALOG;

export function isKnownWidget(type: string): boolean {
  return Object.prototype.hasOwnProperty.call(CATALOG, type);
}

/** Every registered type, for error messages that tell the user what exists. */
export function knownWidgetTypes(): string[] {
  return Object.keys(CATALOG);
}

/**
 * The span rule for a type, falling back for anything unregistered.
 *
 * An unknown component still occupies space — it renders a placeholder saying
 * what was asked for — so it needs a width like everything else.
 */
export function spanRuleFor(type: string): SpanRule {
  return CATALOG[type]?.span ?? FALLBACK_RULE;
}
