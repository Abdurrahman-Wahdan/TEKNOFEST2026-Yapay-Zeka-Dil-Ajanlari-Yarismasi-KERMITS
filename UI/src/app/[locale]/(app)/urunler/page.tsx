import { setRequestLocale } from "next-intl/server";

import { AppPage } from "@/components/layout/AppPage";
import { CompareTablesBrowser } from "@/components/widgets/CompareTablesBrowser";

/**
 * Ürünler — the "ürün"-category half of the offline comparison-table pool
 * (`dataprep.compare`, `data/_tables/*.json`). Static content built by an
 * agent traversal, not a live bank endpoint — see Kampanyalar for the other
 * half, the fixed two-value split the pipeline enforces on every table.
 */
export default async function UrunlerPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return (
    <AppPage>
      <CompareTablesBrowser category="ürün" />
    </AppPage>
  );
}
