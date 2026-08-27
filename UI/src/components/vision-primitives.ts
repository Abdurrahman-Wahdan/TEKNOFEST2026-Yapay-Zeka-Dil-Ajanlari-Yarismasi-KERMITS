"use client";

/**
 * Typed access to the Vision UI template's **primitives** — the leaf components
 * that render markup and import nothing of the app back.
 *
 * Split out of `vision.ts` on 2026-08-25 to break an import cycle. The barrel in
 * `vision.ts` also casts the template's *composed* pieces (`DashboardNavbar`,
 * `DashboardLayout`, `Footer`), and `DashboardNavbar` renders `Breadcrumbs`,
 * which renders `BrandWordmark`, which needs `VuiTypography`. Taking that one
 * primitive from the full barrel closed a loop:
 *
 *   vision.ts -> DashboardNavbar -> Breadcrumbs -> BrandWordmark -> vision.ts
 *
 * A cycle only throws if the graph is walked in an order that reads a `const`
 * before its initialiser runs, which is why this sat harmless for months and
 * then surfaced as `Cannot access '(default export)' before initialization` on
 * /profile — the one page whose own layout imports `DashboardNavbar` directly,
 * so it enters the loop from the far side.
 *
 * The rule that keeps it broken: **nothing under `examples/` may be cast here.**
 * Those go in `vision.ts`, which may import this file but never the reverse.
 *
 * See `vision.ts` for why the casts exist at all.
 */

import type { ComponentType, ReactNode } from "react";

import VuiBoxRaw from "components/VuiBox";
import VuiButtonRaw from "components/VuiButton";
import VuiInputRaw from "components/VuiInput";
import VuiTypographyRaw from "components/VuiTypography";

/** MUI system props, plus whatever the template component adds on top. */
export type VisionProps = {
  children?: ReactNode;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [prop: string]: any;
};

export type VisionComponent = ComponentType<VisionProps>;

export const VuiBox = VuiBoxRaw as unknown as VisionComponent;
export const VuiTypography = VuiTypographyRaw as unknown as VisionComponent;
export const VuiButton = VuiButtonRaw as unknown as VisionComponent;
export const VuiInput = VuiInputRaw as unknown as VisionComponent;
