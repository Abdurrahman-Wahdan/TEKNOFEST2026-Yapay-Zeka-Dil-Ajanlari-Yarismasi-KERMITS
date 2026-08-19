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

// prop-types is a library for typechecking of props.
import PropTypes from "prop-types";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";

import { BrandWordmark, BRAND_AI } from "@/components/ui/BrandWordmark";

/**
 * The page title.
 *
 * The breadcrumb trail this component is named for is deliberately not rendered.
 * It read `home / chat` directly above a heading that already said `Chat` — the
 * same word twice, and a trail is only worth its line when the hierarchy is deep
 * enough to navigate. This app's routes are all one level under the root, so
 * every trail was `home / <the title>`.
 *
 * Restoring it means putting back the `MuiBreadcrumbs` block above the heading,
 * along with these imports:
 *
 *   import { Link } from "vision/router";
 *   import { Breadcrumbs as MuiBreadcrumbs } from "@mui/material";
 *   import Icon from "@mui/material/Icon";
 *
 * and `const routes = route.slice(0, -1);` for the intermediate links.
 *
 * `icon` and `route` are still accepted, and still required by `propTypes`, so
 * that every call site keeps working untouched and a restore is a change to this
 * file alone. They are simply unused for now.
 */
// eslint-disable-next-line no-unused-vars
function Breadcrumbs({ icon, title, route, light = false, brand = false }) {
  return (
    /*
      `data-page-title` is a positioning hook, not styling. /chat's history menu
      lines its left edge up with where the title text actually starts, and the
      toolbar's own padding sits between the toolbar edge and this element -- so
      anchoring to the toolbar left the menu 16px further left than the word.
    */
    /*
      Flex, so whatever is inside is vertically centred rather than sitting on a
      baseline.

      The toolbar row centres this box, but an inline child aligns to its line
      box's baseline, not to the box's middle -- which left 8px of space above the
      wordmark and 4px below, so the text rendered 2px lower than the buttons
      beside it and the header read as two slightly different lines.
    */
    <VuiBox
      mr={{ xs: 0, xl: 8 }}
      display="flex"
      alignItems="center"
      data-page-title
    >
      {/*
        The assistant's page is headed by the brand, not by its route segment.

        `brand` is an explicit prop rather than a check on `route` inside here:
        this component heads every page in the app, and a hidden special case for
        one path is the kind of thing that surprises whoever adds the next page.
      */}
      {brand ? (
        <BrandWordmark fontSize={16}>{BRAND_AI}</BrandWordmark>
      ) : (
        <VuiTypography
          fontWeight="bold"
          textTransform="capitalize"
          variant="h6"
          color={light ? "white" : "dark"}
          noWrap
        >
          {title.replace("-", " ")}
        </VuiTypography>
      )}
    </VuiBox>
  );
}

// Typechecking props for the Breadcrumbs
Breadcrumbs.propTypes = {
  icon: PropTypes.node.isRequired,
  title: PropTypes.string.isRequired,
  route: PropTypes.oneOfType([PropTypes.string, PropTypes.array]).isRequired,
  light: PropTypes.bool,
  /** Head the page with the brand wordmark instead of the route's title. */
  brand: PropTypes.bool,
};

export default Breadcrumbs;
