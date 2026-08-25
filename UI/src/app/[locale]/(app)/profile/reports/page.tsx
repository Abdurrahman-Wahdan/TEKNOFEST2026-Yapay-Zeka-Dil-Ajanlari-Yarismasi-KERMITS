"use client";

import { use } from "react";

import Card from "@mui/material/Card";

import { RequireAuth } from "@/components/layout/RequireAuth";
import { ReportsBrowser } from "@/components/widgets/ReportsBrowser";
import {
  DashboardLayout,
  Footer,
  VuiBox,
  VuiTypography,
} from "@/components/vision";
import Header from "layouts/profile/components/Header";
import { useAuth } from "@/lib/auth";
import { useTranslations } from "next-intl";

/**
 * The Reports tab: everything the user's automations have produced.
 *
 * Its own route rather than a tab index in state, because a report needs an
 * address — the notification bell links straight to one, and a reader who
 * reloads or bookmarks it has to land back on the same report.
 *
 * `searchParams` is read here and passed down as `initialReportId`, following
 * `/urunler` and `/kampanyalar`. That is what makes a deep link correct in the
 * *first* paint; `useSearchParams` inside the browser component would need a
 * Suspense boundary and would render the list for a frame before switching.
 *
 * A client component, unlike its sibling pages, because it renders the same
 * `Header` the overview does — the tabs have to stay on screen and stay in step
 * with the URL, and that component reads `useAuth` and `usePathname`.
 */
export default function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ rapor?: string }>;
}) {
  const { rapor } = use(searchParams);

  return (
    <RequireAuth>
      <ReportsShell initialReportId={rapor ?? null} />
    </RequireAuth>
  );
}

function ReportsShell({ initialReportId }: { initialReportId: string | null }) {
  const { user } = useAuth();
  const t = useTranslations("reports");

  return (
    <DashboardLayout>
      <Header
        name={user?.display_name || ""}
        email={user?.email || ""}
      />
      <VuiBox mt={4} mb={3}>
        <Card sx={{ px: 3, py: 3 }}>
          <VuiBox display="flex" flexDirection="column" gap="4px" mb={2}>
            <VuiTypography variant="lg" color="white" fontWeight="bold">
              {t("title")}
            </VuiTypography>
            <VuiTypography color="text" variant="button" fontWeight="regular">
              {t("subtitle")}
            </VuiTypography>
          </VuiBox>
          <ReportsBrowser initialReportId={initialReportId} />
        </Card>
      </VuiBox>
      <Footer />
    </DashboardLayout>
  );
}
