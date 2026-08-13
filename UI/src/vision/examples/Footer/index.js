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

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";

// `item` removed from the VuiBox tags below: it is a Grid prop, and VuiBox is a
// Box, so it fell through to the DOM and React warned
// "Received `true` for a non-boolean attribute `item`". Box ignores it for
// layout, so nothing moves. This warning is present in the original template too.
function Footer() {
  return (
    <VuiBox
      display="flex"
      flexDirection={{ xs: "column", lg: "row" }}
      justifyContent="space-between"
      direction="row"
      component="footer"
      py={2}
      pb={0}
    >
      <VuiBox xs={12} sx={{ textAlign: "center" }}>
        <VuiTypography
          variant="button"
          sx={{ textAlign: "center", fontWeight: "400 !important" }}
          color="white"
        >
          Create by Kermits
        </VuiTypography>
      </VuiBox>
    </VuiBox>
  );
}

export default Footer;
