"use client";

import { useEffect, useRef } from "react";

import { VuiBox } from "@/components/vision";
import type { AgentMessage, ChatStage, ChatStatus } from "@/lib/chat/types";

import { ChatMessage } from "./ChatMessage";

/**
 * The scrolling transcript.
 *
 * The scroll behaviour is the fiddly part. An answer grows continuously while it
 * streams, so "scroll to the bottom on new content" would fight anyone trying to
 * read back through the conversation -- every token would yank them down again.
 *
 * So it follows the bottom only while the user is already near it. Scrolling up
 * releases the follow; scrolling back down re-arms it. This is what a chat client
 * does, and it is why the check is against the live scroll position rather than a
 * piece of state set on send.
 */
export function ChatMessageList({
  messages,
  status,
  stage,
  compact = false,
}: {
  messages: AgentMessage[];
  status: ChatStatus;
  /** What the agent is doing, for the label on the last, empty bubble. */
  stage?: ChatStage;
  compact?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  /** Whether to keep the newest content in view. Released by scrolling away. */
  const followRef = useRef(true);

  const streaming = status === "streaming" || status === "submitted";
  const lastId = messages.at(-1)?.id;
  // The growing answer's length, so the effect re-runs as tokens land and not
  // only when a message is added.
  const lastLength = messages.at(-1)?.parts.reduce(
    (n, p) => n + (p.type === "text" ? p.text.length : 0),
    0,
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !followRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [lastId, lastLength, messages.length]);

  return (
    <VuiBox
      ref={scrollRef}
      onScroll={() => {
        const el = scrollRef.current;
        if (!el) return;
        // 48px of slack: the bottom is "reached" a little before it is exact,
        // otherwise a stream that outpaces the scroll by a pixel drops the follow
        // and the answer runs off the bottom of the view.
        const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
        followRef.current = distance < 48;
      }}
      flexGrow={1}
      px={compact ? 1.5 : 2}
      py={compact ? 1.5 : 3}
      sx={{ minHeight: 0, overflowY: "auto", overscrollBehavior: "contain" }}
    >
      <VuiBox
        display="flex"
        flexDirection="column"
        gap={compact ? 1.5 : 2.5}
        sx={{ maxWidth: 720, mx: "auto", width: "100%" }}
      >
        {messages.map((message, index) => (
          <ChatMessage
            key={message.id}
            message={message}
            streaming={streaming && index === messages.length - 1}
            isLast={index === messages.length - 1}
            stage={stage}
            compact={compact}
          />
        ))}
      </VuiBox>
    </VuiBox>
  );
}
