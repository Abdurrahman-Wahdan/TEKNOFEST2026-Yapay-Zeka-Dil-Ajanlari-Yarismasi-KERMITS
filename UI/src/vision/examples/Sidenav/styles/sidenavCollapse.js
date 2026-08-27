/*!

=========================================================
* Vision UI Free React - v1.0.0
=========================================================

* Product Page: https://www.creative-tim.com/product/vision-ui-free-react
* Copyright 2021 Creative Tim (https://www.creative-tim.com/)
* Licensed under MIT (https://github.com/creativetimofficial/vision-ui-free-react/blob/master LICENSE.md)

* Design and Coded by Simmmple & Creative Tim

=========================================================

* The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

*/

function collapseItem(theme, ownerState) {
  const { palette, transitions, boxShadows, borders, functions } = theme;
  const { active, transparentSidenav, miniSidenav } = ownerState;

  const { transparent, white, sidenav } = palette;
  const { xxl } = boxShadows;
  const { borderRadius } = borders;
  const { pxToRem } = functions;

  return {
    background: active ? sidenav.button : transparent.main,
    color: white.main,
    display: "flex",
    alignItems: "center",
    width: "100%",
    // Collapsed, the row is symmetrical so the icon lands on the rail's centre
    // line. Keeping the expanded 16px left inset would push every glyph off
    // centre by the difference, which is very visible in a single column of
    // icons with nothing else to line up against.
    padding: miniSidenav
      ? `${pxToRem(10.8)} 0`
      : `${pxToRem(10.8)} ${pxToRem(12.8)} ${pxToRem(10.8)} ${pxToRem(16)}`,
    justifyContent: miniSidenav ? "center" : "flex-start",
    margin: `0 ${pxToRem(16)}`,
    borderRadius: borderRadius.lg,
    cursor: "pointer",
    userSelect: "none",
    whiteSpace: "nowrap",
    // Was duplicated across a plain value and an `xl` override that computed the
    // same thing; the rail is width-independent now, so one rule covers it.
    boxShadow: active && transparentSidenav ? xxl : "none",
    transition: transitions.create(["box-shadow", "padding"], {
      easing: transitions.easing.easeInOut,
      duration: transitions.duration.shorter,
    }),
  };
}

/**
 * The drawer icon's slot — a layout box only, with nothing drawn behind the
 * glyph.
 *
 * The template sat every icon on a 32px rounded chip: a filled tile with a
 * `md` shadow, brand-coloured when the row was active and `sidenav.button`
 * when it was not. Two nested containers to say one thing. The row itself
 * already carries the active state — `collapseItem` above gives it a
 * background and a radius, and the label goes medium-weight — so the chip was
 * repeating that a second time, and the icons read as buttons in a list of
 * links rather than as icons.
 *
 * The 32px box and `placeItems: center` stay: they are what hold the glyphs on
 * a common axis so the labels line up down the drawer. Only the paint is gone.
 */
function collapseIconBox(theme, ownerState) {
  const { palette, transitions, functions } = theme;
  const { color } = ownerState;
  const { pxToRem } = functions;

  return {
    // All three, and the `xl` override that used to sit here, are removed
    // rather than set to a transparent colour so nothing re-introduces a chip
    // at a breakpoint.
    background: "transparent",
    backgroundColor: "transparent",
    boxShadow: "none",

    // 32px box around a 20px glyph. The box is the alignment unit — it is what
    // holds every icon on the rail's centre line and keeps the expanded labels
    // on a common left edge — so the glyph grew inside it rather than the box
    // growing with the glyph.
    minWidth: pxToRem(32),
    minHeight: pxToRem(32),
    display: "grid",
    placeItems: "center",
    transition: transitions.create("margin", {
      easing: transitions.easing.easeInOut,
      duration: transitions.duration.standard,
    }),

    // One colour for every icon, active or not. The glyph used to flip to ink
    // when active because it had to read against a filled brand tile; with no
    // tile under it that flip would just make the current page's icon the only
    // black one in a column of blue.
    //
    // Set twice over, because the drawer holds two kinds of glyph. react-icons
    // paints with `fill`; lucide — the phone action rows, and the app's set
    // everywhere outside this template — strokes `currentColor` and declares
    // `fill="none"`. So `color` colours the stroked ones, which otherwise sat on
    // `ListItemIcon`'s own grey while the nav icons above them were blue.
    //
    // The `fill` rule has to skip them: a CSS `fill` beats the `fill="none"`
    // attribute, and an outline glyph filled in solid is a blob, not an icon.
    color: palette[color].main,
    "& svg:not([fill='none']), svg:not([fill='none']) g": {
      fill: palette[color].main,
    },
  };
}

/** The same, for the routes that pass an icon by ligature name rather than a node. */
const collapseIcon = ({ palette }, { color = "info" }) => ({
  color: palette[color].main,
});

function collapseText(theme, ownerState) {
  const { typography, transitions, functions } = theme;
  const { miniSidenav, active } = ownerState;

  const { size, fontWeightMedium, fontWeightRegular } = typography;
  const { pxToRem } = functions;

  return {
    // Not gated on `xl` any more, and not `miniSidenav || miniSidenav` -- the
    // label follows the rail at every width, because the rail exists at every
    // width now.
    //
    // Faded and zero-width rather than unmounted: the text stays in the DOM so
    // the collapse animates as the label shrinking away, and so a screen reader
    // still reaches the accessible name through the link.
    // `opacity`, not `overflow: hidden`, is what hides the label — and it has to
    // be. The `& span` below carries the template's `line-height: 0`, so the
    // glyphs paint outside their own box; clipping the box therefore erases the
    // label even when it is supposed to be visible. Zero opacity hides it
    // wherever it paints, and zero max-width takes back the space.
    opacity: miniSidenav ? 0 : 1,
    maxWidth: miniSidenav ? 0 : "100%",
    marginLeft: miniSidenav ? 0 : pxToRem(12.8),
    transition: transitions.create(["opacity", "margin", "max-width"], {
      easing: transitions.easing.easeInOut,
      duration: transitions.duration.standard,
    }),

    "& span": {
      fontWeight: active ? fontWeightMedium : fontWeightRegular,
      fontSize: size.sm,
      lineHeight: 0,
    },
  };
}

export { collapseItem, collapseIconBox, collapseIcon, collapseText };
