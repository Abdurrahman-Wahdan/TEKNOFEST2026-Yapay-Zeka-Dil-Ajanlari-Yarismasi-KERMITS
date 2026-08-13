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

/* eslint-disable react/prop-types */
// @mui material components
import Icon from "@mui/material/Icon";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";
import VuiProgress from "components/VuiProgress";

// Images
import AdobeXD from "examples/Icons/AdobeXD";
import Atlassian from "examples/Icons/Atlassian";
import Slack from "examples/Icons/Slack";
import Spotify from "examples/Icons/Spotify";
import Jira from "examples/Icons/Jira";
import Invision from "examples/Icons/Invision";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoSpotify = "/vision/images/small-logos/logo-spotify.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoInvesion = "/vision/images/small-logos/logo-invision.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoJira = "/vision/images/small-logos/logo-jira.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoSlack = "/vision/images/small-logos/logo-slack.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoWebDev = "/vision/images/small-logos/logo-webdev.svg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoXD = "/vision/images/small-logos/logo-xd.svg";
function Completion({ value, color }) {
  return (
    <VuiBox display="flex" flexDirection="column" alignItems="flex-start">
      <VuiTypography variant="button" color="white" fontWeight="medium" mb="4px">
        {value}%&nbsp;
      </VuiTypography>
      <VuiBox width="8rem">
        <VuiProgress value={value} color={color} sx={{ background: "#242628" }} label={false} />
      </VuiBox>
    </VuiBox>
  );
}

const action = (
  <Icon sx={{ cursor: "pointer", fontWeight: "bold" }} fontSize="small">
    more_vert
  </Icon>
);

export default {
  columns: [
    { name: "project", align: "left" },
    { name: "budget", align: "left" },
    { name: "status", align: "left" },
    { name: "completion", align: "center" },
    { name: "action", align: "center" },
  ],

  rows: [
    {
      project: (
        <VuiBox display="flex" alignItems="center">
          <AdobeXD size="20px" />
          <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
            Chakra Vision UI Version
          </VuiTypography>
        </VuiBox>
      ),
      budget: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          $14,000
        </VuiTypography>
      ),
      status: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          Working
        </VuiTypography>
      ),
      completion: <Completion value={60} color="info" />,
      action,
    },
    {
      project: (
        <VuiBox display="flex" alignItems="center">
          <Atlassian size="20px" />
          <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
            Add Progress Track
          </VuiTypography>
        </VuiBox>
      ),
      budget: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          $3,000
        </VuiTypography>
      ),
      status: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          Done
        </VuiTypography>
      ),
      completion: <Completion value={100} color="info" />,
      action,
    },
    {
      project: (
        <VuiBox display="flex" alignItems="center">
          <Slack size="20px" />
          <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
            Fix Platform Errors
          </VuiTypography>
        </VuiBox>
      ),
      budget: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          Not set
        </VuiTypography>
      ),
      status: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          Canceled
        </VuiTypography>
      ),
      completion: <Completion value={30} color="info" />,
      action,
    },
    {
      project: (
        <VuiBox display="flex" alignItems="center">
          <Spotify size="20px" />
          <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
            Launch our Mobile App
          </VuiTypography>
        </VuiBox>
      ),
      budget: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          $32,000
        </VuiTypography>
      ),
      status: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          Canceled
        </VuiTypography>
      ),
      completion: <Completion value={0} color="info" />,
      action,
    },
    {
      project: (
        <VuiBox display="flex" alignItems="center">
          <Jira size="20px" />
          <VuiTypography pl="16px" color="white" variant="button" fontWeight="medium">
            Add the New Pricing Page
          </VuiTypography>
        </VuiBox>
      ),
      budget: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          $2,300
        </VuiTypography>
      ),
      status: (
        <VuiTypography variant="button" color="white" fontWeight="medium">
          Done
        </VuiTypography>
      ),
      completion: <Completion value={100} color="info" />,
      action,
    },
  ],
};
