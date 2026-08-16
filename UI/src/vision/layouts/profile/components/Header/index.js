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

import AppBar from "@mui/material/AppBar";
// @mui material components
import Card from "@mui/material/Card";
import Grid from "@mui/material/Grid";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
// Images
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const burceMars = "/vision/images/avatar-simmmple.png";
// Vision UI Dashboard React base styles
import breakpoints from "assets/theme/base/breakpoints";
import VuiAvatar from "components/VuiAvatar";
// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiButton from "components/VuiButton";
import VuiTypography from "components/VuiTypography";
// Vision UI Dashboard React icons
import { IoCube } from "react-icons/io5";
import { IoDocument } from "react-icons/io5";
import { IoBuild } from "react-icons/io5";
import { IoLogOut } from "react-icons/io5";
// Vision UI Dashboard React example components
import DashboardNavbar from "examples/Navbars/DashboardNavbar";
import { useEffect, useState } from "react";

import { useRouter } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";

function Header({ name = "Mark Johnson", email = "mark@simmmple.com" }) {
  const [tabsOrientation, setTabsOrientation] = useState("horizontal");
  const [tabValue, setTabValue] = useState(0);
  const { logout } = useAuth();
  const router = useRouter();

  const handleSignOut = () => {
    logout();
    router.replace("/login");
  };

  useEffect(() => {
    // A function that sets the orientation state of the tabs.
    function handleTabsOrientation() {
      return window.innerWidth < breakpoints.values.lg
        ? setTabsOrientation("vertical")
        : setTabsOrientation("horizontal");
    }

    /** 
     The event listener that's calling the handleTabsOrientation function when resizing the window.
    */
    window.addEventListener("resize", handleTabsOrientation);

    // Call the handleTabsOrientation function to set the state with the initial value.
    handleTabsOrientation();

    // Remove event listener on cleanup
    return () => window.removeEventListener("resize", handleTabsOrientation);
  }, [tabsOrientation]);

  const handleSetTabValue = (event, newValue) => setTabValue(newValue);

  return (
    <VuiBox position="relative">
      <DashboardNavbar light />
      <Card
        sx={{
          px: 3,
          mt: 2,
        }}
      >
        <Grid
          container
          alignItems="center"
          justifyContent="center"
          sx={({ breakpoints }) => ({
            [breakpoints.up("xs")]: {
              gap: "16px",
            },
            [breakpoints.up("xs")]: {
              gap: "0px",
            },
            [breakpoints.up("xl")]: {
              gap: "0px",
            },
          })}
        >
          <Grid
            item
            xs={12}
            md={1.7}
            lg={1.5}
            xl={1.2}
            xxl={0.8}
            display="flex"
            sx={({ breakpoints }) => ({
              [breakpoints.only("sm")]: {
                justifyContent: "center",
                alignItems: "center",
              },
            })}
          >
            <VuiAvatar
              src={burceMars}
              alt="profile-image"
              variant="rounded"
              size="xl"
              shadow="sm"
            />
          </Grid>
          <Grid item xs={12} md={4.3} lg={4} xl={3.8} xxl={7}>
            <VuiBox
              height="100%"
              mt={0.5}
              lineHeight={1}
              display="flex"
              flexDirection="column"
              sx={({ breakpoints }) => ({
                [breakpoints.only("sm")]: {
                  justifyContent: "center",
                  alignItems: "center",
                },
              })}
            >
              <VuiTypography variant="lg" color="white" fontWeight="bold">
                {name}
              </VuiTypography>
              <VuiTypography variant="button" color="text" fontWeight="regular">
                {email}
              </VuiTypography>
            </VuiBox>
          </Grid>
          <Grid
            item
            xs={12}
            md={6}
            lg={6.5}
            xl={6}
            xxl={4}
            sx={{ ml: "auto" }}
            display="flex"
            alignItems="center"
            justifyContent="flex-end"
            gap={2}
          >
            <AppBar position="static">
              <Tabs
                orientation={tabsOrientation}
                value={tabValue}
                onChange={handleSetTabValue}
                sx={{ background: "transparent", display: "flex", justifyContent: "flex-end" }}
              >
                <Tab label="OVERVIEW" icon={<IoCube color="white" size="16px" />} />
                <Tab label="TEAMS" icon={<IoDocument color="white" size="16px" />} />
                <Tab label="PROJECTS" icon={<IoBuild color="white" size="16px" />} />
              </Tabs>
            </AppBar>
            {/* `variant="text" color="error"` matches the destructive action
                already established on the billing screen (Bill's "DELETE"
                button) — a plain text-and-icon button, not a bordered pill,
                so it reads as part of this toolbar instead of fighting it. */}
            <VuiButton
              variant="text"
              color="error"
              onClick={handleSignOut}
              sx={{ display: "flex", alignItems: "center", whiteSpace: "nowrap" }}
            >
              <IoLogOut size="16px" style={{ marginRight: "4px" }} />
              Sign Out
            </VuiButton>
          </Grid>
        </Grid>
      </Card>
    </VuiBox>
  );
}

export default Header;
