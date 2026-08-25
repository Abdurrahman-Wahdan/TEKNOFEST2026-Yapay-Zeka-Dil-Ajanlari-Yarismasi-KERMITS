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
// The template's stock portrait ("Burce Mars") is gone from this slot -- it was
// a stranger's face standing in for the signed-in user. The brand mark is the
// honest thing to put there until there are real profile pictures to upload:
// it says whose product this is rather than pretending to say who you are.
import { BRAND_LOGO } from "@/components/ui/brand";
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
// Went with the template's PROJECTS tab; kept so restoring a third tab is an
// uncomment rather than a hunt for which glyph it used.
// import { IoBuild } from "react-icons/io5";
import { IoLogOut } from "react-icons/io5";
// Vision UI Dashboard React example components
import DashboardNavbar from "examples/Navbars/DashboardNavbar";
import { useEffect, useState } from "react";

import { useTranslations } from "next-intl";

import { usePathname, useRouter } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";

/**
 * The two profile pages, in tab order. `path` is locale-less -- `@/i18n/navigation`
 * adds the prefix -- and matches the App Router folder under `(app)`.
 */
const TABS = [
  { key: "tabOverview", path: "/profile", icon: IoCube },
  { key: "tabReports", path: "/profile/reports", icon: IoDocument },
];

function Header({ name = "Mark Johnson", email = "mark@simmmple.com" }) {
  const t = useTranslations("nav");
  const tp = useTranslations("profile");
  const [tabsOrientation, setTabsOrientation] = useState("horizontal");
  const { logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  /*
    Derived from the URL rather than held in state.

    The tabs used to be `useState(0)` and navigate nowhere -- three labels that
    moved an underline. Now they are navigation, and the selected tab has to be
    whichever page is actually open: a link from the notification bell lands
    directly on /profile/reports, and a tab index in state would show Genel
    while Raporlar was on screen.

    `findLast` so the longer path wins -- /profile/reports starts with /profile,
    and matching first would select Genel on both pages.
  */
  const active = TABS.findLastIndex((tab) => pathname.startsWith(tab.path));
  const tabValue = active === -1 ? 0 : active;

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

  const handleSetTabValue = (event, newValue) => router.push(TABS[newValue].path);

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
            {/* `contain`, and padding to keep it off the corners: the mark is
                301x225 on a transparent background, and MUI's Avatar sets
                `object-fit: cover` on its img -- which on a 74px square crops
                the wordmark off both ends of the logo. The tile behind it is
                `--muted` so a transparent PNG still reads as a deliberate
                avatar rather than a floating image, in either theme. */}
            <VuiAvatar
              src={BRAND_LOGO}
              alt=""
              variant="rounded"
              size="xl"
              shadow="sm"
              sx={{
                backgroundColor: "var(--muted)",
                padding: "10px",
                "& img": { objectFit: "contain" },
              }}
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
                {/*
                  Two tabs, not the template's three. TEAMS and PROJECTS
                  described a product this is not; the pages behind them were
                  demo content by people who do not exist. `IoBuild` went with
                  PROJECTS -- it is still imported above, commented, so putting a
                  third tab back is an uncomment rather than a hunt for which
                  glyph it used.
                */}
                {TABS.map((tab) => (
                  <Tab
                    key={tab.key}
                    label={tp(tab.key)}
                    icon={<tab.icon color="white" size="16px" />}
                  />
                ))}
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
              {/* `nav.signOut`, the same key the drawer's own Sign Out row
                  reads. Two sign-out controls are on screen together on this
                  page, and they were reading "Çıkış yap" and "Sign Out". */}
              {t("signOut")}
            </VuiButton>
          </Grid>
        </Grid>
      </Card>
    </VuiBox>
  );
}

export default Header;
