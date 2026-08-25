import { setRequestLocale } from "next-intl/server";

import { AppPage } from "@/components/layout/AppPage";
import { CompareTablesBrowser } from "@/components/widgets/CompareTablesBrowser";
import { TABLE_PARAM } from "@/lib/table-url";

/**
 * Kampanyalar — the "kampanya"-category half of the offline comparison-table
 * pool (`dataprep.compare`, `data/_tables/*.json`). See Ürünler for the other
 * half, the fixed two-value split the pipeline enforces on every table.
 *
 * `searchParams` is read here rather than with `useSearchParams` inside the
 * browser component so a link to one table renders that table in the first paint
 * instead of the grid. It also keeps the client component out of a Suspense
 * boundary, which `useSearchParams` would need. The address is written by
 * `dataprep/stamp_table_urls.py` onto every table's point in the
 * `compare_tables` collection, so the assistant can link straight to it.
 *
 * A `?tablo=` that names nothing shows the table card's own load-failed state
 * with the way back to the grid, which is what a stale link should do.
 */
export default async function KampanyalarPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  const table = (await searchParams)[TABLE_PARAM];
  const tableId = typeof table === "string" ? table : null;

  return (
    <AppPage>
      <CompareTablesBrowser
        // A campaign detail and the campaign list are different state
        // identities even though they share one route component.
        key={`kampanya:${tableId ?? "list"}`}
        category="kampanya"
        initialTableId={tableId}
      />
    </AppPage>
  );
}
