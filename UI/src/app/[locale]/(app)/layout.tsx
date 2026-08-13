import { AppRouterCacheProvider } from "@mui/material-nextjs/v15-appRouter";
import { setRequestLocale } from "next-intl/server";

import VisionApp from "@/vision/VisionApp";
import { VisionUIControllerProvider } from "@/vision/context";

/**
 * The Vision UI shell: sidenav drawer, configurator, theme.
 *
 * `AppRouterCacheProvider` is what makes emotion work with the App Router — it
 * flushes MUI's styles into the server-rendered HTML via `useServerInsertedHTML`.
 * Without it every MUI component ships unstyled on first paint and snaps into
 * place on hydration.
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

  return (
    <AppRouterCacheProvider options={{ key: "mui" }}>
      <VisionUIControllerProvider>
        <VisionApp>{children}</VisionApp>
      </VisionUIControllerProvider>
    </AppRouterCacheProvider>
  );
}
