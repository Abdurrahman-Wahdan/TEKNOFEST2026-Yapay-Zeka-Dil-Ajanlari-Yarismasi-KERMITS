"use client";

import type { ReactNode } from "react";

import {
  DashboardLayout,
  DashboardNavbar,
  Footer,
  VuiBox,
} from "@/components/vision";

/**
 * The frame every page in the app sits in.
 *
 * Exactly the structure the template's own pages use — `DashboardLayout` for
 * the sidenav and page frame, `DashboardNavbar` for the breadcrumb and tools,
 * `Footer` at the bottom. Any page that skips it renders bare inside the shell
 * and reads as a different application, which is how `/banks` and `/compare`
 * ended up looking bolted on.
 *
 * `TopicPage` is this plus the produced-components body; use this one directly
 * for pages with their own content.
 */
export function AppPage({ children }: { children: ReactNode }) {
  return (
    <DashboardLayout>
      {/* `DashboardLayout` is a plain padded box with no height of its own, so
          a short page (nothing to compare yet, an empty table) left the
          footer sitting right under the content instead of at the bottom of
          the screen. This wrapper is the sticky-footer pattern: fill at least
          the viewport, let the content grow, and the footer lands at the
          bottom whether it is pushed there by content or by the space below it. */}
      <VuiBox display="flex" flexDirection="column" minHeight="100vh">
        <DashboardNavbar />
        <VuiBox py={3} flexGrow={1}>{children}</VuiBox>
        <Footer />
      </VuiBox>
    </DashboardLayout>
  );
}
