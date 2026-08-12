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

// prop-types is a library for typechecking of props
// @mui material components
import Card from "@mui/material/Card";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const billingCard = "/vision/images/billing-background-card.png";
// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";
import PropTypes from "prop-types";
import { RiMastercardFill } from "react-icons/ri";

function MasterCard({ number, valid, cvv }) {
  const numbers = [...`${number}`];

  if (numbers.length < 16 || numbers.length > 16) {
    throw new Error(
      "Invalid value for the prop number, the value for the number prop shouldn't be greater than or less than 16 digits"
    );
  }

  const num1 = numbers.slice(0, 4).join("");
  const num2 = numbers.slice(4, 8).join("");
  const num3 = numbers.slice(8, 12).join("");
  const num4 = numbers.slice(12, 16).join("");

  return (
    <Card sx={{ background: `url('${billingCard}')`, backdropfilter: "blur(31px)" }}>
      <VuiBox p={2} pt={0}>
        <VuiBox
          color="onImage"
          lineHeight={0}
          display="flex"
          justifyContent="space-beetween"
          alignItems="center"
          width="100%"
          sx={{ width: "100%" }}
        >
          <VuiTypography color="onImage" variant="lg" fontWeight="bold" mr="auto">
            Vision UI
          </VuiTypography>
          <RiMastercardFill size="48px" color="white" />
        </VuiBox>
        <VuiTypography
          variant="h4"
          color="onImage"
          mt="auto"
          fontWeight="medium"
          sx={({ breakpoints }) => ({
            mt: 8,
            pb: 1,
            // The template only shrinks the number at `sm` (425–767), so on a
            // phone the 16 digits wrap and the last one drops to its own line.
            [breakpoints.down("sm")]: {
              fontSize: "18px",
            },
            [breakpoints.only("sm")]: {
              fontSize: "22px",
            },
          })}
        >
          {num1}&nbsp;&nbsp;&nbsp;{num2}&nbsp;&nbsp;&nbsp;{num3}&nbsp;&nbsp;&nbsp;{num4}
        </VuiTypography>
        <VuiBox display="flex" justifyContent="space-between" alignItems="center">
          <VuiBox display="flex" alignItems="center">
            <VuiBox mr={3} lineHeight={1}>
              <VuiTypography variant="xxs" color="onImage" fontWeight="medium" opacity={0.8}>
                VALID THRU
              </VuiTypography>
              <VuiTypography
                variant="h6"
                color="onImage"
                fontWeight="medium"
                textTransform="capitalize"
              >
                {valid}
              </VuiTypography>
            </VuiBox>
            <VuiBox lineHeight={1}>
              <VuiTypography variant="xxs" color="onImage" fontWeight="medium" opacity={0.8}>
                CVV
              </VuiTypography>
              <VuiTypography variant="h6" color="onImage" fontWeight="medium">
                {cvv}
              </VuiTypography>
            </VuiBox>
          </VuiBox>
        </VuiBox>
      </VuiBox>
    </Card>
  );
}

// Typechecking props for the MasterCard
MasterCard.propTypes = {
  color: PropTypes.oneOf([
    "white",
    "secondary",
    "info",
    "success",
    "warning",
    "error",
    "dark",
    "text",
  ]),
};

export default MasterCard;
