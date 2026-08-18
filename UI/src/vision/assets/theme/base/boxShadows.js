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

/**
 * The base box-shadow styles for the Vision UI Dashboard  Material.
 * You can add new box-shadow using this file.
 * You can customized the box-shadow for the entire Vision UI Dashboard  Material using thie file.
 */

// Vision UI Dashboard React Base Styles

// Vision UI Dashboard React Helper Functions
import boxShadow from "assets/theme/functions/boxShadow";

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  // `shadow`, not `black`: shadow ink follows the mode so a shadow carries the
  // same weight on a light page as on a dark one. See the note on `shadow` in
  // `base/colors.js` — every alpha below is unchanged and mode-independent,
  // which only works because the ink is the thing that moves.
  const { shadow, white, info, inputColors, tabs } = colors;

  return {
    xs: boxShadow([0, 2], [9, -5], shadow.main, 0.15),
    sm: boxShadow([0, 5], [10, 0], shadow.main, 0.12),
    md: `${boxShadow([0, 4], [6, -1], shadow.light, 0.12)}, ${boxShadow(
      [0, 2],
      [4, -1],
      shadow.light,
      0.07
    )}`,
    lg: `${boxShadow([0, 8], [26, -4], shadow.light, 0.15)}, ${boxShadow(
      [0, 8],
      [9, -5],
      shadow.light,
      0.06
    )}`,
    xl: boxShadow([0, 23], [45, -11], shadow.light, 0.25),
    xxl: boxShadow([0, 20], [27, 0], shadow.main, 0.05),
    inset: boxShadow([0, 1], [2, 0], shadow.main, 0.075, "inset"),
    navbarBoxShadow: `${boxShadow([0, 0], [1, 1], white.main, 0.9, "inset")}, ${boxShadow(
      [0, 20],
      [27, 0],
      shadow.main,
      0.05
    )}`,
    buttonBoxShadow: {
      main: `${boxShadow([0, 4], [7, -1], shadow.main, 0.11)}, ${boxShadow(
        [0, 2],
        [4, -1],
        shadow.main,
        0.07
      )}`,
      stateOf: `${boxShadow([0, 3], [5, -1], shadow.main, 0.09)}, ${boxShadow(
        [0, 2],
        [5, -1],
        shadow.main,
        0.07
      )}`,
      stateOfNotHover: boxShadow([0, 0], [0, 3.2], info.main, 0.5),
    },
    inputBoxShadow: {
      focus: boxShadow([0, 0], [0, 2], inputColors.boxShadow, 1),
      error: boxShadow([0, 0], [0, 2], inputColors.error, 0.6),
      success: boxShadow([0, 0], [0, 2], inputColors.success, 0.6),
    },
    sliderBoxShadow: {
      thumb: boxShadow([0, 1], [13, 0], shadow.main, 0.2),
    },
    tabsBoxShadow: {
      indicator: boxShadow([0, 1], [5, 1], tabs.indicator.boxShadow, 1),
    },
  };
};
