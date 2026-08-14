"use client";

/**
 * Typed access to the Vision UI template's components.
 *
 * Marked "use client" because everything it re-exports is: the template's
 * components use hooks (`DashboardLayout` reaches for `useEffect`). Without the
 * directive, a Server Component importing so much as `VuiTypography` from here
 * pulls that in and the build fails. With it, a server page can render these
 * directly, as long as the props it passes are serializable.
 *
 * `src/vision/` is the Creative Tim template: plain JavaScript, typed only by
 * PropTypes at runtime. TypeScript resolves those imports to the real .js files
 * and infers a bare `forwardRef` with no props from them, so
 * `<VuiBox py={3}>…</VuiBox>` in a .tsx file fails to compile — not because it
 * is wrong, but because there is nothing to check it against. Ambient
 * declarations do not help: a module that resolves to a real file wins over
 * them, and `declare module` on it becomes an augmentation instead.
 *
 * So the cast happens once, here, and app code imports the template from this
 * one place. The alternative — casting at twenty call sites, or hand-writing
 * MUI's system prop surface and re-syncing it whenever the template moves — is
 * worse in both directions.
 *
 * The looseness stops at this file. Everything the app actually owns — the
 * component contract, the layout engine, the filters — is strictly typed in
 * `src/lib/`.
 */

import type { ComponentType, ReactNode } from "react";

import VuiBoxRaw from "components/VuiBox";
import VuiButtonRaw from "components/VuiButton";
import VuiInputRaw from "components/VuiInput";
import VuiTypographyRaw from "components/VuiTypography";
import DashboardLayoutRaw from "examples/LayoutContainers/DashboardLayout";
import DashboardNavbarRaw from "examples/Navbars/DashboardNavbar";
import FooterRaw from "examples/Footer";

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
export const DashboardLayout = DashboardLayoutRaw as unknown as VisionComponent;
export const DashboardNavbar = DashboardNavbarRaw as unknown as VisionComponent;
export const Footer = FooterRaw as unknown as VisionComponent;
