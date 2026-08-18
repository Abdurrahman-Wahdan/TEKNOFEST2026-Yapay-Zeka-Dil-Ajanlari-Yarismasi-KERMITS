import { AppRouterCacheProvider } from "@mui/material-nextjs/v15-appRouter";
import { setRequestLocale } from "next-intl/server";
import { cookies } from "next/headers";

import { ChatProvider } from "@/lib/chat/ChatProvider";
import VisionApp from "@/vision/VisionApp";
import { VisionUIControllerProvider } from "@/vision/context";

/**
 * The Vision UI shell: sidenav drawer, configurator, theme.
 *
 * `AppRouterCacheProvider` is what makes emotion work with the App Router — it
 * flushes MUI's styles into the server-rendered HTML via `useServerInsertedHTML`.
 * Without it every MUI component ships unstyled on first paint and snaps into
 * place on hydration.
 *
 * The drawer's collapsed state is read here, on the server, for the same
 * reason: its width is an emotion class, so seeding it from the cookie is what
 * makes a collapsed drawer render collapsed in the very first HTML instead of
 * rendering wide and snapping narrow once the client can read storage.
 *
 * `ChatProvider` wraps `VisionApp` rather than sitting inside it because the
 * conversation has two consumers on opposite sides of that boundary: the popup,
 * which `VisionApp` renders, and the /chat page, which arrives as `children`.
 * Mounted here it also outlives navigation between the pages inside it, so an
 * open conversation survives a click from /compare to /urunler.
 */
export default async function AppLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  const miniSidenav = (await cookies()).get("tf26.sidenav")?.value === "mini";

  return (
    <AppRouterCacheProvider options={{ key: "mui" }}>
      <VisionUIControllerProvider initialMiniSidenav={miniSidenav}>
        <ChatProvider>
          <VisionApp>{children}</VisionApp>
        </ChatProvider>
      </VisionUIControllerProvider>
    </AppRouterCacheProvider>
  );
}
