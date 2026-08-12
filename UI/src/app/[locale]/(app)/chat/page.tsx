import { getTranslations, setRequestLocale } from "next-intl/server";

import { Card, CardGrid } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { RequireAuth } from "@/components/layout/RequireAuth";

/** Route stub. The chat panel itself is not built yet. */
export default async function ChatPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("chat");

  return (
    <RequireAuth>
      <PageHeader title={t("title")} />
      <CardGrid>
        <Card span={4}>
          <p>{t("placeholder")}</p>
        </Card>
      </CardGrid>
    </RequireAuth>
  );
}
