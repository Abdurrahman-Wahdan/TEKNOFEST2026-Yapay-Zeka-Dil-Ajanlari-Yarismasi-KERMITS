import { getTranslations, setRequestLocale } from "next-intl/server";

import { Card, CardGrid } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { RequireAuth } from "@/components/layout/RequireAuth";

/** Route stub. The preferences form is not built yet. */
export default async function SettingsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("settings");

  return (
    <RequireAuth>
      <PageHeader title={t("title")} />
      <CardGrid>
        <Card title={t("preferences")} span={4}>
          <p>{t("banksOfInterest")}</p>
        </Card>
      </CardGrid>
    </RequireAuth>
  );
}
