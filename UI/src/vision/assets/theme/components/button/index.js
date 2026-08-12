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

// Vision UI Dashboard React Button Styles
import root from "assets/theme/components/button/root";
import contained from "assets/theme/components/button/contained";
import outlined from "assets/theme/components/button/outlined";
import text from "assets/theme/components/button/text";

// Takes `colors` so the theme can be rebuilt per light/dark mode.
export default (colors) => {
  const containedStyles = contained(colors);
  const outlinedStyles = outlined(colors);
  const textStyles = text(colors);

  return {
    defaultProps: {
      disableRipple: true,
    },
    styleOverrides: {
      root: { ...root(colors) },
      contained: { ...containedStyles.base },
      containedSizeSmall: { ...containedStyles.small },
      containedSizeLarge: { ...containedStyles.large },
      containedPrimary: { ...containedStyles.primary },
      containedSecondary: { ...containedStyles.secondary },
      outlined: { ...outlinedStyles.base },
      outlinedSizeSmall: { ...outlinedStyles.small },
      outlinedSizeLarge: { ...outlinedStyles.large },
      outlinedPrimary: { ...outlinedStyles.primary },
      outlinedSecondary: { ...outlinedStyles.secondary },
      text: { ...textStyles.base },
      textSizeSmall: { ...textStyles.small },
      textSizeLarge: { ...textStyles.large },
      textPrimary: { ...textStyles.primary },
      textSecondary: { ...textStyles.secondary },
    },
  };
};
