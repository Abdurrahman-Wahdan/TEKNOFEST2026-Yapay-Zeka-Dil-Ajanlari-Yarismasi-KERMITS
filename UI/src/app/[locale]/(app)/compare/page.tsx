import { setRequestLocale } from "next-intl/server";

import { AppPage } from "@/components/layout/AppPage";
import { Comparator } from "@/components/widgets/Comparator";

/**
 * Karşılaştır — the live comparison tool.
 *
 * Deterministic software over the bank endpoints: every figure is a bank's own
 * answer, and the two exceptions (a derived conversion, a spread) are labelled
 * where they appear. Nothing on this page comes from a model.
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
