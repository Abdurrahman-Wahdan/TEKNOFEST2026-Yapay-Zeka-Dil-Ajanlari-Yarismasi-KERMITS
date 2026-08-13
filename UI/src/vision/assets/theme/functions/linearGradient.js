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
  The linearGradient() function helps you to create a linear gradient color background
 */

function linearGradient(color, colorState, angle, ...extraStops) {
  if (angle === undefined) {
    angle = 310;
  }
  // `extraStops` is how a caller adds a third (or later) stop — e.g. a final
  // stop that fades to fully transparent, so a gradient's tail dissolves into
  // whatever is behind it instead of ending as a flat band of colour.
  // Existing two-stop callers are unaffected: filter(Boolean) drops the
  // `undefined` an unused optional stop passes through as.
  const stops = [color, colorState, ...extraStops].filter(Boolean);
  return `linear-gradient(${angle}deg, ${stops.join(", ")})`;
}

export default linearGradient;
