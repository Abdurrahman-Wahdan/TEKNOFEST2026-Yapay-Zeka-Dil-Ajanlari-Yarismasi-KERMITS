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
export function AppPage({
  children,
  fullHeight,
  brandTitle,
  headerActions,
}: {
  children: ReactNode;
  /**
   * Head the page with the `KERMİTS AI` wordmark instead of its route title.
   *
   * Only /chat wants this: the page *is* the assistant, so naming it after its
   * URL segment ("chat") says less than the brand does.
   */
  brandTitle?: boolean;
  /**
   * Controls rendered in the navbar beside the page title.
   *
   * /chat uses it for its new-chat and history buttons -- they belong to the page,
   * not to the app chrome, so they arrive from the page rather than being wired
   * into the shared navbar.
   */
  headerActions?: ReactNode;
  /**
   * For a page that fills the viewport and scrolls inside itself, rather than
   * growing downward — /chat is the only one so far.
   *
   * The default sticky-footer layout below cannot express that: `minHeight:
   * 100vh` lets content push the page taller, which is the opposite of what a
   * chat transcript needs. A transcript has to be *bounded* by the viewport so
   * its own scroll container is the thing that scrolls and the composer stays
   * pinned at the bottom. Given `minHeight`, the composer just gets pushed off
   * the end of a growing page.
   *
   * The `Footer` goes with it. Below a pinned composer there is no reachable
   * space to put one, and reserving room for a footer nobody can scroll to only
   * shortens the transcript.
   */
  fullHeight?: boolean;
}) {
  return (
    <DashboardLayout>
      {/* `DashboardLayout` is a plain padded box with no height of its own, so
          a short page (nothing to compare yet, an empty table) left the
          footer sitting right under the content instead of at the bottom of
          the screen. This wrapper is the sticky-footer pattern: fill at least
          the viewport, let the content grow, and the footer lands at the
          bottom whether it is pushed there by content or by the space below it. */}
      <VuiBox
        display="flex"
        flexDirection="column"
        {...(fullHeight ? { height: "100vh" } : { minHeight: "100vh" })}
      >
        <DashboardNavbar brand={brandTitle} actions={headerActions} />
        <VuiBox
          py={3}
          flexGrow={1}
          /**
           * The page's content, and the only stable handle on it.
           *
           * There was no way to name "the page minus the chrome" before this: the
           * drawer, navbar and assistant panel are all `position: fixed` siblings,
           * and neither this box nor `DashboardLayout` exposed a ref or an id. Two
           * things need it -- the selection locator, which scopes its
           * nearest-heading search here so the drawer's own headings stop winning
           * on every page, and the page capture, which must not photograph the
           * chrome floating over the page.
           */
          data-page-root=""
          // `minHeight: 0` is what actually lets a flex child scroll: without it
          // the child's automatic minimum size is its content, so it grows past
          // the container instead of overflowing inside it.
          {...(fullHeight ? { sx: { minHeight: 0, overflow: "hidden" } } : {})}
        >
          {children}
        </VuiBox>
        {!fullHeight && <Footer />}
      </VuiBox>
    </DashboardLayout>
  );
}
