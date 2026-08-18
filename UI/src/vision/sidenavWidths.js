/**
 * The drawer's two widths, and the gap the content keeps from it.
 *
 * These used to be retyped in four places that had to agree by hand —
 * `examples/Sidenav/SidenavRoot.js`, `examples/LayoutContainers/DashboardLayout`,
 * and `assets/theme/components/sidenav.js` — where the layout offset was written
 * as the already-summed 120/274 rather than as the drawer width plus a gutter, so
 * changing the rail meant finding and re-adding two numbers somewhere else.
 *
 * Plain numbers, not `pxToRem` strings: MUI treats a bare number as px, and both
 * consumers need to do arithmetic on them.
 */

/** Expanded: wide enough for an icon and its label. */
export const SIDENAV_WIDTH = 250;

/**
 * Collapsed. The rail still shows every nav icon, so it has to fit the 32px
 * icon box centred in the paper with its 16px margin either side.
 */
export const SIDENAV_RAIL = 96;

/** The paper's own 16px margin, plus a little air before the content starts. */
export const SIDENAV_GUTTER = 24;
