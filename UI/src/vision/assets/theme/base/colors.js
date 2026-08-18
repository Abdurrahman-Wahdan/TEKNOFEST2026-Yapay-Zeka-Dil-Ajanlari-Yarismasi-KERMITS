/** 

=========================================================
* Vision UI  React - v1.0.0
=========================================================

* Product Page: https://www.creative-tim.com/product/vision-ui-pro-react
* Copyright 2021 Creative Tim (https://www.creative-tim.com/)

* Design and Coded by Simmmple & Creative Tim

=========================================================

* The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Visionware.

*/

import { palette } from "@/lib/palette";

/**
 * `#rrggbb` + alpha -> `rgba(r, g, b, a)`.
 *
 * The template's card and sidenav gradients are translucent surfaces. Their
 * alphas are part of the design and are kept exactly; only the colour under
 * them now comes from the palette, so they follow the theme.
 */
function hexToRgba(hex, alpha) {
  const value = hex.replace("#", "");
  const full =
    value.length === 3
      ? value
          .split("")
          .map((c) => c + c)
          .join("")
      : value;
  const int = parseInt(full, 16);
  // eslint-disable-next-line no-bitwise
  return `rgba(${(int >> 16) & 255}, ${(int >> 8) & 255}, ${int & 255}, ${alpha})`;
}

/** `#rrggbb` channels, as numbers. */
function channels(hex) {
  const value = hex.replace("#", "");
  const full =
    value.length === 3
      ? value
          .split("")
          .map((c) => c + c)
          .join("")
      : value;
  const int = parseInt(full, 16);
  // eslint-disable-next-line no-bitwise
  return [(int >> 16) & 255, (int >> 8) & 255, int & 255];
}

/**
 * `amount` of `tint` blended into `base`, as opaque hex.
 *
 * The template's gradients are two hardcoded colours a designer picked by eye.
 * Deriving the second stop from the first keeps that depth while making it a
 * function of the palette — a tinted surface rather than a fixed navy that only
 * belongs in the dark theme.
 */
function mix(base, tint, amount) {
  const a = channels(base);
  const b = channels(tint);
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * amount));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * The base colors for the Vision UI Dashboard  Material.
 * You can add new color using this file.
 * You can customized the colors for the entire Vision UI Dashboard  Material using thie file.
 */

/**
 * The template's colour roles, filled from our palette for the given mode.
 *
 * Every key, gradient angle and alpha of the original is kept — only the values
 * come from `@/lib/palette`, which is generated from tailwind.css. That is what
 * makes the theme and the rest of the app the same palette rather than two that
 * happen to look alike, and what lets the light/dark toggle reach MUI at all:
 * the module used to export a frozen object built once at import time.
 */
export default function makeColors(mode = "dark") {
  const p = palette(mode);
  const isDark = mode === "dark";

  // Which palette entry plays the page and which plays the card.
  //
  // Dark takes them as the palette states them: page #000, card #17181c — the
  // card is 23 levels LIGHTER than the page, and that is the whole reason it
  // reads as a lit, translucent sheet lying over the page rather than a shape
  // drawn on it.
  //
  // Light mode's palette has the two the other way round — background #ffffff,
  // card #f7f8f8 — so a card came out 8 levels DARKER than its page: not a
  // sheet lifted off the page but a grey rectangle stamped into it. No amount
  // of tuning the gradient fixes that, because the direction is the problem,
  // not the amount; the earlier attempts here tried alpha and then a blue tint
  // and neither could make a darker-than-page card look lifted.
  //
  // So light swaps them: a very slightly toned page with white cards lifted
  // off it. That is the same relationship dark already has, and the way light
  // UIs are normally built. Both values still come from the palette — this
  // chooses between them, it does not invent a colour.
  const page = isDark ? p.background : p.card;
  const surface = isDark ? p.card : p.background;
  // The recess behind a raised surface — the sidenav's far stop, the cover and
  // bill gradients. Still carries a little of the palette's blue in light so a
  // large panel reads as a designed surface rather than an unpainted one; the
  // plain `Card` no longer uses it and takes the neutral `cardFar` below.
  const surfaceDeep = isDark ? "#0a0a0c" : mix(surface, p.primary, 0.09);
  const surfaceRaised = isDark ? "#1c1e24" : "#ffffff";
  // The far end of the plain `Card` gradient: the card colour walked halfway to
  // the page. Neutral by construction — it moves the card's own colour toward
  // the page's rather than blending in a third one, so the gradient reads as a
  // translucent sheet in both modes instead of a tint. In dark it lands within
  // two levels of the hand-picked `surfaceDeep` it replaces there.
  const cardFar = mix(surface, page, 0.5);

  return {

    // Vision UI Colors
    background: {
      default: page,
    },

    sidenav: {
      // The selected nav row's pill.
      //
      // Dark can use the raised surface as-is: #1c1e24 against a near-black
      // drawer reads as lifted. Light cannot — the drawer's own gradient starts
      // at rgba(255,255,255,0.94), so a #ffffff pill is white on white and the
      // selected page had no visible marker at all.
      //
      // So light gets a pale wash of the brand instead, mixed from the palette
      // like every other derived colour here rather than picked. It also agrees
      // with the nav icons, which are already `info` blue — the row and its glyph
      // then say "selected" together instead of the glyph saying it alone.
      //
      // Only this token changes. `surfaceRaised` is shared with `raised`, `focus`
      // and three gradients, and those are correct as they are.
      button: isDark ? surfaceRaised : mix(surface, p.primary, 0.14),
    },

    text: {
      main: p["muted-foreground"],
      focus: p["primary-foreground"],
    },

    transparent: {
      main: "transparent",
    },

    // `white` is the template's name for *ink* — the colour of text, icon
    // strokes and chart bars sitting on a surface. Vision UI is a dark-only
    // design, so its ink happened to be white and the name stuck; ~200 call
    // sites say `color="white"` meaning "primary text". Bound to the palette
    // foreground it inverts with the mode, which is what makes light mode
    // readable at all. The places that need a white *surface* or white ink on
    // a filled brand colour use `surfaces` and `onBrand` below.
    white: {
      main: p.foreground,
      focus: p.foreground,
    },

    // Ink that sits on a filled brand colour — a label inside a blue button,
    // an icon on the info-coloured circle. Always light, in both modes.
    onBrand: {
      main: p["primary-foreground"],
      focus: p["primary-foreground"],
    },

    // Ink over artwork. The hero card's photograph is dark in both themes, so
    // this text cannot follow the mode the way `white` does — it would turn
    // near-black on a near-black image.
    onImage: {
      main: "#ffffff",
      focus: "#ffffff",
    },

    onImageMuted: {
      main: "rgba(255, 255, 255, 0.72)",
      focus: "rgba(255, 255, 255, 0.72)",
    },

    // The surface ramp, page outwards. `raised` is the one a card sits on when
    // it needs to read as lifted; `deep` is the recess behind it.
    surfaces: {
      page,
      card: surface,
      raised: surfaceRaised,
      deep: surfaceDeep,
      muted: p.muted,
      // The wash under a hovered table row. The one translucent member of the
      // ramp, and it has to be: the card beneath it is a gradient, so an
      // opaque value would flatten the far end of it into a rectangle.
      //
      // Built from the ink for the same reason `borderCol.navbar` below is.
      // The hardcoded `rgba(255, 255, 255, 0.03)` this replaces lifted the
      // dark card (#17181c) by about seven levels and the light one (#f7f8f8)
      // by none at all — which is why table hover simply did not exist in
      // light mode. Following the foreground darkens a light card and lightens
      // a dark one by the same amount: +12 levels in dark, -12 in light.
      //
      // Light takes the lower alpha despite its wider channel spread, because
      // a dark wash on a near-white surface is more salient per level than a
      // light wash on a near-black one. Both sit under `borderCol.navbar`'s
      // 0.18/0.12 on purpose: a hairline must read as a line, a hover wash
      // only has to read as a region.
      hover: hexToRgba(p.foreground, isDark ? 0.06 : 0.05),
    },

    brand: {
      main: p.primary,
      focus: p.primary,
    },

    // Near-black, for the things that must be dark in both modes: the tooltip
    // body (which carries `onImage` ink) and the slider rail. NOT shadows —
    // those take `shadow` below.
    black: {
      light: "#141414",
      main: "#0b0d10",
      focus: "#0b0d10",
    },

    // Shadow ink, and the last place the two modes were not behaving alike.
    //
    // `boxShadows.js` builds every shadow from this at a fixed alpha, so what a
    // shadow actually does on screen is `alpha × distance(ink, page)`. Pinned
    // to near-black it was 11 levels from a black page and 236 from a light
    // one: the same declaration that is invisible in dark cast a grey halo
    // under all 252 cards in light. That halo is the "extra shadow" — a card
    // reading as a stamped-on rectangle rather than a sheet lying on the page.
    //
    // Binding it to the page instead would cast a *white* shadow and do
    // nothing, which is the trap the old comment here warned about. The fix is
    // neither: hold the DISTANCE constant and let the direction follow the
    // mode. Each entry sits the same number of levels off its own page as the
    // hand-picked dark value did — main ~11, light ~20 — so every shadow in the
    // file now lands with the same weight in both modes without touching a
    // single alpha.
    shadow: {
      light: isDark ? "#141414" : mix(page, p.foreground, 0.09),
      main: isDark ? "#0b0d10" : mix(page, p.foreground, 0.05),
      focus: isDark ? "#0b0d10" : mix(page, p.foreground, 0.05),
    },

    primary: {
      main: p.primary,
      focus: isDark ? "#7fd3ff" : "#7fd3ff",
    },

    secondary: {
      main: surface,
      focus: surfaceRaised,
    },

    lightblue: {
      main: p.ring,
      focus: p.ring,
    },

    orange: {
      main: p["chart-3"],
      focus: p["chart-3"],
    },

    grey: {
      100: "#edf2f7",
      200: "#e2e8f0",
      300: "#cbd5e0",
      400: p["muted-foreground"],
      500: "#718096",
      600: "#4a5568",
      700: "#2d3748",
      800: "#1a202a",
      900: "#171923",
    },

    borderCol: {
      main: p["sidebar-border"],
      red: p.destructive,
      // The template's translucent white hairline. Against a light page it is
      // invisible; against a dark one it is the only thing separating the
      // navbar from the content — so it follows the ink instead.
      navbar: hexToRgba(p.foreground, isDark ? 0.18 : 0.12),
    },

    // Other colors
    info: {
      main: p.primary,
      focus: p.ring,
      charts: {
        100: p.primary,
        200: "#1885cc",
        300: "#1885cc",
        400: "#12659e",
        500: "#0d4d78",
        600: "#083454",
      },
    },

    success: {
      main: p["chart-2"],
      focus: "#33d69f",
    },

    warning: {
      main: p["chart-3"],
      focus: "#ffe27a",
    },

    error: {
      main: p.destructive,
      focus: "#ff6b63",
    },

    light: {
      main: p.muted,
      focus: p.accent,
    },

    dark: {
      main: p.border,
      focus: surface,
      body: page,
    },

    gradients: {
      // A barely-there sheen across the sticky navbar. Built from the ink so
      // it lightens a dark bar and darkens a light one, rather than always
      // adding white.
      navbar: {
        deg: "123.64deg",
        main: `${hexToRgba(p.foreground, 0)} -22.38%`,
        state: `${hexToRgba(p.foreground, isDark ? 0.04 : 0.03)} 70.38%`,
      },

      sidenav: {
        deg: "127.09",
        main: `${hexToRgba(surface, 0.94)} 19.41%`,
        state: `${hexToRgba(surfaceDeep, 0.49)} 76.65%`,
      },

      // The lit edge on Vision's "gradient border" cards. It is a highlight,
      // so in light mode it has to darken rather than brighten — a white edge
      // on a white card is the invisible hairline the dark theme never shows.
      borderLight: {
        angle: "94.43% 69.43% at 50% 50%",
        main: `${hexToRgba(p.foreground, isDark ? 1 : 0.22)} 0%`,
        state: `${hexToRgba(p.foreground, 0)} 100%`,
      },

      borderDark: {
        angle: "69.43% 69.43% at 50% 50%",
        main: `${hexToRgba(p.foreground, isDark ? 1 : 0.22)} 0%`,
        state: `${hexToRgba(p.foreground, 0)} 100%`,
      },

      cover: {
        deg: "159.02",
        main: `${surfaceDeep} 14.25%`,
        state: `${surface} 56.45%`,
        stateSecondary: `${page} 86.14%`,
      },

      cardDark: {
        deg: "126.97",
        main: `${hexToRgba(surface, 0.74)} 28.26%`,
        state: `${hexToRgba(surfaceDeep, 0.71)} 91.2%`,
      },

      cardLight: {
        deg: "127.09",
        main: `${hexToRgba(surface, 0.94)} 19.41%`,
        state: `${hexToRgba(surfaceDeep, 0.49)} 76.65%`,
      },

      // The plain `Card` everywhere in the dashboard — the surface nearly
      // every page is built out of.
      //
      // One formula for both modes, and that is the whole point. A card is a
      // translucent sheet lying on the page, so it may differ from the page in
      // LIGHTNESS but never in HUE: `cardFar` is the card colour walked halfway
      // to the page, which darkens in dark mode and lightens in light mode
      // without introducing a colour of its own.
      //
      // Two earlier attempts branched on `isDark` and tinted the light state
      // stop with `primary`, on the reasoning that a neutral light card shows
      // no visible gradient. True — but neither does the dark one: composited
      // over their pages, dark's stops land 22 and 5 levels off black, and the
      // gradient is not what makes that card read as a card. What the tint
      // bought instead was a hue shift — the light state stop sat 15 levels off
      // white in red and 3 in blue — and a few percent of luminance is
      // invisible where a hue shift is not, so the card stopped reading as a
      // sheet over the page and started reading as a separate blue slab.
      //
      // Definition comes from the border and the shadow, which is what they
      // are for — and from the card sitting a few levels above its page, which
      // is what `page`/`surface` at the top of this function guarantee in both
      // modes.
      //
      // `fade` is a third stop at 100%. Without it a two-stop gradient holds
      // its last colour flat from 76.65% to the edge, so the final ~23% of the
      // card is a constant band ending at the rounded corner — the hard edge
      // that reads as "not blended". Both modes get it now: in dark it is worth
      // ~5 levels and invisible either way, and one unbranched formula is worth
      // more than saving a stop nobody can see.
      card: {
        deg: "127.09",
        main: `${hexToRgba(surface, 0.94)} 19.41%`,
        state: `${hexToRgba(cardFar, 0.49)} 76.65%`,
        fade: `${hexToRgba(cardFar, 0)} 100%`,
      },

      // Dropdown surfaces. The template's hardcoded navy is one of the black
      // blocks that survive into light mode; tinted from the palette's accent
      // instead, so a menu reads as a raised surface in either theme.
      menu: {
        deg: "126.97",
        main: `${surfaceRaised} 28.26%`,
        state: `${mix(surfaceRaised, p.primary, isDark ? 0.16 : 0.07)} 91.2%`,
      },

      cardContent: {
        deg: "126.97",
        main: `${surface} 28.26%`,
        state: `${surfaceDeep} 91.2%`,
      },

      box: {
        deg: "126.97",
        main: `${hexToRgba(surface, 0.74)} 28.26%`,
        state: `${hexToRgba(surfaceDeep, 0.71)} 91.2%`,
      },

      bill: {
        deg: "127.09",
        main: `${hexToRgba(surfaceRaised, 0.94)} 19.41%`,
        state: `${hexToRgba(surfaceDeep, 0.49)} 76.65%`,
      },

      primary: {
        deg: "97.89",
        main: p.primary,
        state: isDark ? "#7fd3ff" : "#7fd3ff",
      },

      secondary: {
        main: mix(surface, p.foreground, isDark ? 0.22 : 0.1),
        state: mix(surface, p.foreground, isDark ? 0.42 : 0.22),
      },

      // The sidenav wordmark. Hardcoded white, which is why the brand vanishes
      // against a light sidenav — it fades the ink out instead.
      logo: {
        deg: "97.89",
        main: `${p.foreground} 70.67%`,
        state: `${hexToRgba(p.foreground, 0)} 108.55%`,
      },

      info: {
        main: p.primary,
        state: isDark ? "#7fd3ff" : "#7fd3ff",
      },

      success: {
        main: p["chart-2"],
        state: "#9df3cd",
      },

      warning: {
        main: p["chart-3"],
        state: "#ffe27a",
      },

      error: {
        main: p.destructive,
        state: p.destructive,
      },

      light: {
        main: p.muted,
        state: p.accent,
      },

      dark: {
        main: surfaceDeep,
        state: p.border,
      },
    },

    socialMediaColors: {
      facebook: {
        main: "#3b5998",
        dark: "#344e86",
      },

      twitter: {
        main: "#55acee",
        dark: "#3ea1ec",
      },

      instagram: {
        main: "#125688",
        dark: "#0e456d",
      },

      linkedin: {
        main: "#0077b5",
        dark: "#00669c",
      },

      pinterest: {
        main: "#cc2127",
        dark: "#b21d22",
      },

      youtube: {
        main: "#e52d27",
        dark: "#d41f1a",
      },

      vimeo: {
        main: "#1ab7ea",
        dark: "#13a3d2",
      },

      slack: {
        main: "#3aaf85",
        dark: "#329874",
      },

      dribbble: {
        main: "#ea4c89",
        dark: "#e73177",
      },

      github: {
        main: "#24292e",
        dark: "#171a1d",
      },

      reddit: {
        main: "#ff4500",
        dark: "#e03d00",
      },

      tumblr: {
        main: "#35465c",
        dark: "#2a3749",
      },
    },

    alertColors: {
      primary: {
        main: "#7928ca",
        state: "#d6006c",
        border: "#efb6e2",
      },

      secondary: {
        main: "#627594",
        state: "#8ca1cb",
        border: "#dadee6",
      },

      info: {
        main: "#2152ff",
        state: "#02c6f3",
        border: "#b9ecf8",
      },

      success: {
        main: "#17ad37",
        state: "#84dc14",
        border: "#daf3b9",
      },

      warning: {
        main: "#f53939",
        state: "#fac60b",
        border: "#fef1c2",
      },

      error: {
        main: "#ea0606",
        state: "#ff3d59",
        border: "#f9b4b4",
      },

      light: {
        main: "#ced4da",
        state: "#d1dae6",
        border: "#f8f9fa",
      },

      dark: {
        main: "#141727",
        state: "#2c3154",
        border: "#c2c8d1",
      },
    },

    badgeColors: {
      primary: {
        basic: "#805ad5",
        background: "#f883dd",
        text: "#a3017e",
      },

      secondary: {
        basic: "#5974a2",
        background: "#e4e8ed",
        text: "#5974a2",
      },

      info: {
        basic: "#4299e1",
        background: "#abe9f7",
        text: "#08a1c4",
      },

      success: {
        basic: "#01b574",
        background: "#c9fbd5",
        text: "#01b574",
      },

      warning: {
        basic: "#ffb547",
        background: "#fef5d3",
        text: "#fbc400",
      },

      error: {
        basic: "#e31a1a",
        background: "#fc9797",
        text: "#bd0000",
      },

      light: {
        basic: "#ffffff",
        background: "#ffffff",
        text: "#c7d3de",
      },

      dark: {
        basic: "#1E244B",
        background: "#1E244B",
        text: "#fff",
      },
    },

    // The search field and every other input. The template's `#0f1535` navy is
    // the most visible black block left in light mode — it is the one control
    // the eye lands on first in the navbar.
    inputColors: {
      backgroundColor: p.input,
      borderColor: {
        main: hexToRgba(p.foreground, isDark ? 0.3 : 0.16),
        focus: hexToRgba(p.ring, 0.6),
      },
      boxShadow: p.ring,
      error: p.destructive,
      success: p["chart-2"],
    },

    sliderColors: {
      thumb: { borderColor: p.border },
    },

    circleSliderColors: {
      background: p.muted,
    },

    tabs: {
      indicator: { boxShadow: p.border },
    },

    // ApexCharts has its own light/dark switch for tooltips and gradient
    // shading, and it takes the mode name rather than a colour.
    chartTooltipTheme: mode,

    // The hairline around a card: the mode's own ink at one alpha for both.
    //
    // It used to be carried harder in light (0.09 vs 0.06), on the reasoning
    // that Vision separates cards from the page with a glow that only works on
    // a dark background, so a light card needed its edge to do that work. That
    // was compensating for the card being *darker* than its page — with the two
    // the right way round a light card is lifted like a dark one, and the extra
    // 0.03 stopped being separation and became the one hard edge left in a
    // surface built to dissolve. One alpha, and the ink either side of it, puts
    // the two modes within a couple of levels of each other: +12 against a dark
    // card, -14 against a light one.
    cardBorder: hexToRgba(p.foreground, 0.06),
  };
}
