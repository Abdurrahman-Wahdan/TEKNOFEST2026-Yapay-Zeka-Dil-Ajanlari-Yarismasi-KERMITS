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

  // Surfaces. Vision UI is a dark design, so its "raised card" is a lift away
  // from the page in dark and a slight recess in light.
  const surface = p.card;
  // The far end of every card gradient. In light mode a neutral grey turns the
  // whole page into flat off-white, so it carries a little of the palette's
  // blue — enough to read as a designed surface rather than an unpainted one.
  const surfaceDeep = isDark ? "#0a0a0c" : mix(p.card, p.primary, 0.09);
  const surfaceRaised = isDark ? "#1c1e24" : "#ffffff";

  return {

    // Vision UI Colors
    background: {
      default: p.background,
    },

    sidenav: {
      button: surfaceRaised,
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
      page: p.background,
      card: surface,
      raised: surfaceRaised,
      deep: surfaceDeep,
      muted: p.muted,
    },

    brand: {
      main: p.primary,
      focus: p.primary,
    },

    // Shadow ink. Deliberately *not* the palette background: `boxShadows.js`
    // builds every shadow as `black` at low alpha, so binding this to the
    // background makes light mode cast white shadows — which is why the light
    // cards read as flat rectangles with no separation from the page.
    black: {
      light: "#141414",
      main: "#0b0d10",
      focus: "#0b0d10",
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
      body: p.background,
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
        stateSecondary: `${p.background} 86.14%`,
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

      card: {
        deg: "127.09",
        main: `${hexToRgba(surface, 0.94)} 19.41%`,
        state: `${hexToRgba(surfaceDeep, 0.49)} 76.65%`,
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

    // The hairline around a card. Vision UI separates cards from the page with
    // a glow, which only works on a dark background — in light mode the edge
    // has to do that work instead.
    cardBorder: hexToRgba(p.foreground, isDark ? 0.06 : 0.09),
  };
}
