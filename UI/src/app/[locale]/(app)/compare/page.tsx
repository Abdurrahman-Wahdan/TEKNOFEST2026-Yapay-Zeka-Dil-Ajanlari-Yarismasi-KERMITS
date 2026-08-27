import { setRequestLocale } from "next-intl/server";

import { AppPage } from "@/components/layout/AppPage";
import { Comparator } from "@/components/widgets/Comparator";

/**
 * Karşılaştır — the live comparison tool.
 *
 * Deterministic software over the bank endpoints: every figure is a bank's own
 * answer, and the two exceptions (a derived conversion, a spread) are labelled
 * where they appear.
 *
 * One thing on the page is written by a model and it is fenced off: the
 * `LiveOverview` card above the results, which reads the finished table and says
 * what it shows. It computes nothing — it is handed the page as an outline and
 * quotes it back — and it says so on itself, every time. No figure below it ever
 * comes from a model.
 *
 * `CompareFinance` is unmounted rather than deleted — it was the finance-only
 * version of this and still works; the Comparator supersedes it by covering
 * every category the endpoints support.
 */
export default async function ComparePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return (
    <AppPage>
      <Comparator />
    </AppPage>
  );
}
