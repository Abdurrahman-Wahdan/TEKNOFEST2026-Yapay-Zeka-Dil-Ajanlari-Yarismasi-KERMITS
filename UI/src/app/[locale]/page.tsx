import { redirect } from "@/i18n/navigation";

/**
 * The site root. There is no separate marketing page — the dashboard is the
 * product, so `/` goes straight there.
 *
 * `redirect` from `@/i18n/navigation`, not `next/navigation`: the plain one
 * drops the locale prefix and would send an English visitor to the Turkish
 * dashboard.
 */
export default async function RootPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect({ href: "/dashboard", locale });
}
