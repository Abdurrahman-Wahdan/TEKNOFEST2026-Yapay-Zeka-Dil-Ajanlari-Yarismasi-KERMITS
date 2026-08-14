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
      <DashboardNavbar />
      <VuiBox py={3}>{children}</VuiBox>
      <Footer />
    </DashboardLayout>
  );
}
