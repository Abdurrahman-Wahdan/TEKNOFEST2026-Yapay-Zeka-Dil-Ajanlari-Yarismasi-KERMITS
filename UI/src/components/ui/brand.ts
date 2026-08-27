/**
 * The brand's mark, in one place.
 *
 * The same argument `BrandWordmark` makes for the `KERMİTS` lockup, for the
 * image beside it: the path was written out at three call sites — the drawer,
 * the footer and the assistant panel — and the profile header was about to be a
 * fourth. A string repeated four times is a string that gets updated three
 * times, and the failure is silent: one surface keeps rendering the old file
 * until somebody notices the brand is two different marks in one screen.
 *
 * No imports on purpose. The `.js` template files under `src/vision/` read this
 * as happily as the `.tsx` app code does, and a constants module that pulls in a
 * component is how `vision.ts` earned its import cycle.
 */

/** The logo mark. 301×225 with a transparent background — **never** render it
 *  into a square without `objectFit: "contain"`, or it is cropped or stretched. */
export const BRAND_LOGO = "/vision/images/kermits-logo.png";
