import type { ComponentType } from "react";

import { BankRegistry } from "./BankRegistry";

/**
 * The component catalog — the list of tiles a dashboard can be built from.
 *
 * This is the contract the AI Overview page composes against. A saved view is
 * a list of `{type, props}`, where `type` is a key in here, so the model picks
 * from a fixed, known set rather than emitting layout code. Everything a model
 * can produce is therefore something that renders.
 *
 * Adding a widget means: build it as a self-contained component that fetches
 * its own data from its props, then register it below. Nothing else changes.
 *
 * The catalog is intentionally NOT mirrored in Python. The API stores the JSON
 * without validating `type`, because duplicating this list server-side
 * guarantees the two drift; `renderComponent` handles an unknown type visibly
 * instead.
 */
// Each widget has its own prop type; the catalog is the one place they are
// erased so they can live in one map. `never` rather than `any`: a saved view's
// props arrive as untyped JSON and are validated by the widget that receives
// them, so the map itself has nothing useful to say about their shape.
export const CATALOG: Record<string, ComponentType<never>> = {
  BankRegistry,
};

export type WidgetType = keyof typeof CATALOG;

export function isKnownWidget(type: string): boolean {
  return Object.prototype.hasOwnProperty.call(CATALOG, type);
}
