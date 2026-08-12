import { getTranslations, setRequestLocale } from "next-intl/server";

import { BankRegistry } from "@/components/widgets/BankRegistry";
import { Card, CardGrid } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";

export default async function BanksPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("banks");

  return (
    <>
      <PageHeader title={t("title")} />
      <CardGrid>
        <Card span={4}>
          <BankRegistry />
        </Card>
      </CardGrid>
    </>
  );
}
