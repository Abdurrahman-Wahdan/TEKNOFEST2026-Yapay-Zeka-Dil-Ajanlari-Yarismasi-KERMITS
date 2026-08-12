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

// @mui material components
import { useTheme } from "@mui/material/styles";

import React from "react";

// @mui components
import { Card, Stack } from "@mui/material";

// Vision UI Dashboard assets
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const balance = "/vision/images/billing-background-balance.png";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const Graph = "/vision/images/shapes/graph-billing.svg";

// Vision UI Dashboard components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";

// React icons
import { FaEllipsisH } from "react-icons/fa";
import { MdOutlineDomain } from "react-icons/md";

const CreditBalance = () => {
  const { palette } = useTheme();

  return (
    <Card sx={{ padding: "30px" }}>
      <VuiBox display="flex" flexDirection="column">
        <VuiBox
          mb="32px"
          p="20px"
          display="flex"
          flexDirection="column"
          sx={{ backgroundImage: `url(${balance})`, backgroundSize: "cover", borderRadius: "18px" }}
        >
          <VuiBox display="flex" justifyContent="space-beetween" alignItems="center">
            <VuiTypography variant="caption" color="onImage" fontWeight="medium" mr="auto">
              Credit Balance
            </VuiTypography>
            <FaEllipsisH color="white" size="18px" sx={{ cursor: "pointer" }} />
          </VuiBox>
          <VuiBox display="flex" justifyContent="space-beetween" alignItems="center">
            <VuiTypography variant="h2" color="onImage" fontWeight="bold" mr="auto">
              $25,215
            </VuiTypography>
            <VuiBox component="img" src={Graph} sx={{ width: "58px", height: "20px" }} />
          </VuiBox>
        </VuiBox>
        <VuiTypography color="text" variant="xxs" fontWeight="medium" mb="8px">
          NEWEST
        </VuiTypography>
        <VuiBox display="flex" justifyContent="space-beetween" alignItems="center">
          <Stack direction="row" spacing="10px" mr="auto">
            <VuiBox
              display="flex"
              mr="10px"
              justifyContent="center"
              alignItems="center"
              sx={{
                background: palette.surfaces.raised,
                borderRadius: "50%",
                width: "42px",
                height: "42px",
              }}
            >
              <MdOutlineDomain color={palette.success.main} size="20px" />
            </VuiBox>
            <VuiBox display="flex" flexDirection="column">
              <VuiTypography color="white" variant="button" fontWeight="medium">
                Bill & Taxes
              </VuiTypography>
              <VuiTypography color="text" variant="button" fontWeight="medium">
                Today, 16:36
              </VuiTypography>
            </VuiBox>
          </Stack>
          <VuiTypography variant="button" color="white" fontWeight="bold">
            -$154.50
          </VuiTypography>
        </VuiBox>
      </VuiBox>
    </Card>
  );
};

export default CreditBalance;
