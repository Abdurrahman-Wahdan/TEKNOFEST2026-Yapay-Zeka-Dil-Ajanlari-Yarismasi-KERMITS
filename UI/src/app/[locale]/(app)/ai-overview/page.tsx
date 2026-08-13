import { getTranslations, setRequestLocale } from "next-intl/server";

import { Card, CardGrid } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { RequireAuth } from "@/components/layout/RequireAuth";

/**
 * Where the assistant composes a dashboard for this user.
 *
 * Not built yet, by agreement: the shape is settled -- a saved view is a list
 * of `{type, props}` naming components in `widgets/catalog.ts`, and this page
 * renders whatever the model picked -- but the composing itself is the next
 * piece of work. The plumbing it needs (SavedView storage, the catalog, the
 * unknown-type placeholder) is already in place.
 */
export default async function AiOverviewPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("aiOverview");

  return (
    <RequireAuth>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <CardGrid>
        <Card span={4}>
          <p>{t("empty")}</p>
        </Card>
      </CardGrid>
    </RequireAuth>
  );
}
