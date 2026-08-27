import { setRequestLocale } from "next-intl/server";

import { ChatHistoryMenu } from "@/components/chat/ChatHistoryMenu";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { AppPage } from "@/components/layout/AppPage";
import { RequireAuth } from "@/components/layout/RequireAuth";

/**
 * The assistant, full screen.
 *
 * No `PageHeader`: the empty state carries its own heading, and once there is a
 * transcript the page title is just height taken from the conversation.
 *
 * `emptyState="center"` is what makes this read like a fresh chat client — the
 * composer starts in the middle of the page and moves to the bottom on the first
 * message. The conversation itself lives in `ChatProvider` up in the (app)
 * layout, so arriving here from the popup's expand button shows the conversation
 * already in progress rather than an empty page.
 */
export default async function ChatPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  // Without this the route opts out of static rendering.
  setRequestLocale(locale);

  return (
    <RequireAuth>
      <AppPage fullHeight brandTitle headerActions={<ChatHistoryMenu />}>
        <ChatPanel emptyState="center" autoFocus />
      </AppPage>
    </RequireAuth>
  );
}
