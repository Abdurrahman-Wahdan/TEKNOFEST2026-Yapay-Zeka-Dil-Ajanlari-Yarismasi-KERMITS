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

// Vision UI Dashboard React Base Styles
import linearGradient from "assets/theme/functions/linearGradient";
import borders from "assets/theme/base/borders";
import boxShadows from "assets/theme/base/boxShadows";

// Vision UI Dashboard React Helper Function

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { cardBorder, gradients } = colors;
  const { card } = gradients;
  const { borderWidth, borderRadius } = borders(colors);
  const { xxl } = boxShadows(colors);

  return {
    styleOverrides: {
      root: {
        display: "flex",
        flexDirection: "column",
        background: linearGradient(card.main, card.state, card.deg, card.fade),
        backdropFilter: "blur(120px)",
        position: "relative",
        minWidth: 0,
        padding: "22px",
        wordWrap: "break-word",
        backgroundClip: "border-box",
        border: `${borderWidth[1]} solid ${cardBorder}`,
        borderRadius: borderRadius.xl,
        boxShadow: xxl,
      },
    },
  };
};
