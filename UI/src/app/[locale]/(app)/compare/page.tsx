import { getTranslations, setRequestLocale } from "next-intl/server";

import { CompareFinance } from "@/components/widgets/CompareFinance";
import { PageHeader } from "@/components/ui/PageHeader";

export default async function ComparePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("compare");

  return (
    <>
      <PageHeader title={t("title")} />
      <CompareFinance />
    </>
  );
}
