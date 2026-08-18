"use client";

import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import type { AgentMessage } from "@/lib/chat/types";

/**
 * Streamdown pulls in Shiki, and Shiki is large. Loading it lazily keeps it off
 * the critical path for every dashboard page -- which matters because the popup
 * mounts on all of them, and most visits never open it.
 *
 * `ssr: false` because there is nothing to server-render: assistant text only
 * exists after a client-side stream. The cost is a brief fallback the first time
 * an answer appears, which is why the fallback below is the plain text rather
 * than a spinner -- the words are readable while the renderer arrives.
 */
const AgentMarkdown = dynamic(
  () => import("./AgentMarkdown").then((m) => m.AgentMarkdown),
  {
    ssr: false,
    loading: () => null,
  },
);

export function ChatMessage({
  message,
  streaming,
}: {
  message: AgentMessage;
  /** True when this is the last message and the stream is still open. */
  streaming?: boolean;
}) {
  const t = useTranslations("chat");

  return (
    <VuiBox display="flex" flexDirection="column" gap={1}>
      {message.parts.map((part, index) => {
        if (part.type === "error") {
          return (
            <VuiBox
              key={index}
              px={2}
              py={1.5}
              sx={{
                // Outlined, not filled -- the same rule the app's status pills
                // follow, so an error here reads as the app's error and not as a
                // pasted-in alert.
                border: "1px solid var(--danger)",
                backgroundColor: "var(--danger-subtle)",
                borderRadius: "var(--radius-sm)",
                alignSelf: "flex-start",
                maxWidth: "100%",
              }}
            >
              <VuiTypography variant="button" color="white" fontWeight="medium">
                {part.title ?? t("errorTitle")}
              </VuiTypography>
              <VuiTypography
                variant="button"
                fontWeight="regular"
                color="text"
                sx={{ display: "block", mt: 0.25 }}
              >
                {part.message}
              </VuiTypography>
            </VuiBox>
          );
        }

        if (message.role === "user") {
          return (
            <VuiBox
              key={index}
              alignSelf="flex-end"
              px={2.25}
              py={1.25}
              sx={{
                // ChatGPT's proportions: a rounded rectangle that stops well
                // short of the full column so the turn-taking is legible at a
                // glance, and a fill one step off the page rather than a tinted
                // panel. The earlier version used `--accent` with a border, which
                // in the light theme is a pale blue box and read as a callout
                // rather than as something the user said.
                maxWidth: "75%",
                backgroundColor: "var(--muted)",
                border: "none",
                // Large, but not a pill -- a multi-line question in a pill looks
                // like a button.
                borderRadius: "20px",
                // The user's own words are never markdown: rendering them as
                // markdown would let a stray asterisk silently reformat what they
                // typed. Their newlines are still honoured.
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              <VuiTypography
                variant="button"
                fontWeight="regular"
                color="white"
                sx={{ lineHeight: 1.6 }}
              >
                {part.text}
              </VuiTypography>
            </VuiBox>
          );
        }

        // The assistant, mid-stream, before its first token: nothing to render
        // yet, and an empty bubble would flash on every send.
        if (!part.text) {
          return streaming ? (
            <VuiTypography
              key={index}
              variant="button"
              fontWeight="regular"
              color="text"
              sx={{ opacity: 0.6 }}
            >
              {t("thinking")}
            </VuiTypography>
          ) : null;
        }

        return (
          <VuiBox
            key={index}
            alignSelf="flex-start"
            // No bubble on the assistant's side. Its answers carry tables and code
            // blocks, and a bubble around a table is a box inside a box.
            sx={{ maxWidth: "100%", minWidth: 0, width: "100%" }}
          >
            <AgentMarkdown streaming={streaming}>{part.text}</AgentMarkdown>
          </VuiBox>
        );
      })}
    </VuiBox>
  );
}
