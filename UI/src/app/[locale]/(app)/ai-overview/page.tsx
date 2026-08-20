import { getTranslations, setRequestLocale } from "next-intl/server";

import { AppPage } from "@/components/layout/AppPage";
import { RequireAuth } from "@/components/layout/RequireAuth";
import { PageHeader } from "@/components/ui/PageHeader";
import { SavedViewsBoard } from "@/components/widgets/SavedViewsBoard";

/**
 * Where the assistant keeps the tables it built for this user.
 *
 * Nothing on this page is configured here. A table arrives because the user asked
 * the agent for one, or pressed save on one it had already written — so the page is
 * a consequence of the conversation, and its only control is delete.
 *
 * `RequireAuth` wraps the board rather than sitting beside it, and that nesting is
 * load-bearing: it renders nothing until the session is restored, which is what
 * keeps the `/me/views` request from firing before the access token exists. A 401
 * here is not retried (see `lib/query.tsx`), so that race would be a permanent
 * error state rather than a slow load.
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
    <AppPage>
      <RequireAuth>
        <PageHeader title={t("title")} subtitle={t("subtitle")} />
        <SavedViewsBoard />
      </RequireAuth>
    </AppPage>
  );
}
