import { redirect } from "@/i18n/navigation";

/**
 * The site root. There is no separate marketing page — the product itself is
 * the landing, so `/` goes straight to the first live page in the drawer.
 *
 * That was `/dashboard`, then `/finansman`; both are now unmounted (see
 * `(app)/_unmounted/README.md`), so this points at Karşılaştır — the first
 * entry left in the drawer. It follows the drawer rather than naming a
 * favourite page, so the next unmount only has to change it once.
 *
 * `redirect` from `@/i18n/navigation`, not `next/navigation`: the plain one
 * drops the locale prefix, and an unprefixed `/compare` is not a route — the
 * App Router only has `/[locale]/...` pages.
 */
export default async function RootPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect({ href: "/compare", locale });
}
