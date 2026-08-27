"use client";

import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import { useChat } from "@/lib/chat/ChatProvider";
import { IS_MOCK_TRANSPORT } from "@/lib/chat/transport";

import { ChatComposer } from "./ChatComposer";
import { ChatMessageList } from "./ChatMessageList";

/**
 * A conversation: the transcript, and the composer under it.
 *
 * Both chat surfaces render this. They differ by one prop: the full page passes
 * `emptyState="center"`, which is what produces
 * the behaviour of a fresh page -- the composer sits in the middle of the empty
 * screen, and drops to the bottom as soon as there is a transcript to make room
 * for.
 *
 * That transition is a *layout* consequence of `messages.length`, not an animation
 * or a piece of state: an empty conversation centres the composer, a non-empty one
 * puts the list above it. There is nothing to keep in sync.
 */
export function ChatPanel({
  emptyState = "bottom",
  autoFocus,
  placeholder,
  compact = false,
}: {
  /** Passed straight to the composer: a fixed prompt instead of the examples. */
  placeholder?: string;
  /** `center` floats the composer mid-screen until the first message. */
  emptyState?: "center" | "bottom";
  autoFocus?: boolean;
  /** Condenses transcript typography and spacing for the floating assistant. */
  compact?: boolean;
}) {
  const t = useTranslations("chat");
  const { messages, status, stage } = useChat();

  const isEmpty = messages.length === 0;
  const centred = isEmpty && emptyState === "center";

  return (
    <VuiBox display="flex" flexDirection="column" sx={{ height: "100%", minHeight: 0 }}>
      {centred ? (
        <VuiBox
          flexGrow={1}
          display="flex"
          flexDirection="column"
          alignItems="center"
          justifyContent="center"
          gap={3}
          px={2}
          sx={{ minHeight: 0 }}
        >
          <VuiBox sx={{ textAlign: "center", maxWidth: 560 }}>
            <VuiTypography variant="h4" color="white" fontWeight="bold">
              {t("emptyTitle")}
            </VuiTypography>
            <VuiTypography
              variant="button"
              fontWeight="regular"
              color="text"
              sx={{ display: "block", mt: 1 }}
            >
              {t("emptySubtitle")}
            </VuiTypography>
          </VuiBox>
          <ChatComposer autoFocus={autoFocus} placeholder={placeholder} />
          {IS_MOCK_TRANSPORT && <MockNotice label={t("mockNotice")} />}
        </VuiBox>
      ) : (
        <>
          <ChatMessageList
            messages={messages}
            status={status}
            stage={stage}
            compact={compact}
          />
          <VuiBox px={2} pb={2} sx={{ flexShrink: 0 }}>
            <ChatComposer autoFocus={autoFocus} placeholder={placeholder} />
          </VuiBox>
        </>
      )}
    </VuiBox>
  );
}

/**
 * Says the answers are canned.
 *
 * The agent is not built yet, and a UI that streams fixture text with no notice
 * is a UI that will be demoed as working. Delete this along with the mock -- it
 * disappears on its own once `streamChat` points at the real backend, because
 * `IS_MOCK_TRANSPORT` goes false.
 */
function MockNotice({ label }: { label: string }) {
  return (
    <VuiTypography
      variant="caption"
      color="text"
      sx={{ opacity: 0.7, textAlign: "center", maxWidth: 480 }}
    >
      {label}
    </VuiTypography>
  );
}
