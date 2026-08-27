"use client";

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

import Card from "@mui/material/Card";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";
import Footer from "examples/Footer";
// Vision UI Dashboard React example components
import DashboardLayout from "examples/LayoutContainers/DashboardLayout";
// Overview page components
import Header from "layouts/profile/components/Header";

import { useTranslations } from "next-intl";

import { AutomationComposer } from "@/components/widgets/AutomationComposer";
import { AutomationsBoard } from "@/components/widgets/AutomationsBoard";
import { ProfileStats } from "@/components/widgets/ProfileStats";
import { NotificationEmailSettings } from "@/components/widgets/NotificationEmailSettings";
import { useAuth } from "@/lib/auth";

/**
 * The profile overview: what this user has done, and what they have standing.
 *
 * The template's own body is gone — a Welcome card, a car's mileage and fuel, a
 * PlatformSettings panel of switches wired to nothing, and three "projects" by
 * Elena Morison and Ryan Milly. None of it described this product.
 *
 * **The components are kept, not deleted**, at
 * `layouts/profile/components/{Welcome,CarInformations,PlatformSettings}` and
 * `examples/Cards/{InfoCards/ProfileInfoCard,ProjectCards/DefaultProjectCard}`.
 * They are simply not imported here — the same treatment `vision/routes.js`
 * gives an unmounted page, and for the same reason: `ProfileInfoCard` in
 * particular is the template's worked example of a labelled detail card, and
 * this app will want one.
 *
 * `Header` stays exactly as it was and carries the identity — avatar, name,
 * email, sign out — plus the tabs, which now navigate instead of moving an
 * underline over nothing.
 */
function Overview() {
  // This screen only renders inside <RequireAuth>, so `user` is never null
  // here — the placeholder strings below are the fallback for that contract
  // being violated, not an expected path.
  const { user } = useAuth();
  const t = useTranslations("profile");
  const ta = useTranslations("automations");
  const name = user?.display_name || "Mark Johnson";
  const email = user?.email || "mark@simmmple.com";

  return (
    <DashboardLayout>
      <Header name={name} email={email} />

      <VuiBox mt={4} mb={3} display="flex" flexDirection="column" gap="24px">
        <Section title={t("statsTitle")} subtitle={t("statsSubtitle")}>
          <ProfileStats />
        </Section>

        <Section
          title={t("notificationEmail")}
          subtitle={t("notificationEmailSubtitle")}
        >
          <NotificationEmailSettings />
        </Section>

        {/*
          The composer sits above the list rather than below it. Creating is what
          someone comes to this section to do the first time, and an empty list
          with the box under it puts the only usable control below an empty
          state.
        */}
        <Section title={ta("composerTitle")} subtitle={ta("composerHint")}>
          <AutomationComposer />
        </Section>

        <Section title={ta("title")} subtitle={ta("subtitle")}>
          <AutomationsBoard />
        </Section>
      </VuiBox>

      <Footer />
    </DashboardLayout>
  );
}

/**
 * A titled block on this page.
 *
 * MUI's `Card` from the template rather than `@/components/ui/Card`: this page
 * is inside `DashboardLayout`, whose surfaces are the template's, and mixing the
 * two card styles in one column is visible. The app's own `Card` is right for a
 * `CardGrid` page such as /ai-overview.
 */
function Section({ title, subtitle, children }) {
  return (
    <Card sx={{ px: 3, py: 3 }}>
      <VuiBox display="flex" flexDirection="column" gap="4px" mb={2}>
        <VuiTypography variant="lg" color="white" fontWeight="bold">
          {title}
        </VuiTypography>
        {subtitle && (
          <VuiTypography color="text" variant="button" fontWeight="regular">
            {subtitle}
          </VuiTypography>
        )}
      </VuiBox>
      {children}
    </Card>
  );
}

export default Overview;
