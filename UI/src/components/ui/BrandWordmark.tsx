"use client";

// `vision-primitives`, not the `@/components/vision` barrel: this component is
// rendered by `Breadcrumbs`, which `DashboardNavbar` renders, which the barrel
// casts -- importing the barrel here closes that loop. See the header comment
// in `vision-primitives.ts`.
import { VuiTypography } from "@/components/vision-primitives";

/**
 * The `KERMİTS` lockup, in one place.
 *
 * There were three copies of these six props -- the drawer header, the assistant
 * panel's header, and now the navbar on /chat -- and a gradient wordmark spelled
 * out by hand at each call site is a thing that drifts. One of them gets a weight
 * or a letter-spacing tweak and the brand quietly renders two different ways in
 * one screen.
 *
 * `textGradient` with `color="logo"` is what paints it: the theme's `logo`
 * gradient clipped to the glyphs, which is why the text colour itself is
 * transparent. That mechanism lives in `VuiTypography`.
 */
export function BrandWordmark({
  children,
  fontSize = 14,
}: {
  children: React.ReactNode;
  /**
   * Only the size varies between call sites, and only a little. Everything else is
   * fixed on purpose -- see above.
   */
  fontSize?: number;
}) {
  return (
    <VuiTypography
      variant="button"
      textGradient={true}
      color="logo"
      fontSize={fontSize}
      letterSpacing={2}
      fontWeight="medium"
      sx={{ whiteSpace: "nowrap" }}
    >
      {children}
    </VuiTypography>
  );
}

/**
 * What the assistant is called.
 *
 * The drawer's own mark is still `brandName="KERMİTS"` from `vision/VisionApp.js`
 * -- the template takes it as a prop, so it cannot read this. The two are separate
 * strings, and renaming the brand means changing both.
 */
export const BRAND_AI = "KERMİTS AI";
