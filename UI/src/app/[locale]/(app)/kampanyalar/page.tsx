import { setRequestLocale } from "next-intl/server";

import { AppPage } from "@/components/layout/AppPage";
import { CompareTablesBrowser } from "@/components/widgets/CompareTablesBrowser";

/**
 * Kampanyalar — the "kampanya"-category half of the offline comparison-table
 * pool (`dataprep.compare`, `data/_tables/*.json`). See Ürünler for the other
 * half, the fixed two-value split the pipeline enforces on every table.
 */
export default async function KampanyalarPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return (
    <AppPage>
      <CompareTablesBrowser category="kampanya" />
    </AppPage>
  );
}
