"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { streamChat } from "./transport";
import type { AgentMessage, ChatStatus } from "./types";
import { useAttachments } from "./useAttachments";

/**
 * The conversation, held above both chat surfaces.
 *
 * This is what makes the popup's expand button work. The popup lives inside
 * `VisionApp` and the full page is a route -- two different trees. If each owned
 * its own messages, expanding would navigate to an empty page and the user would
 * watch their conversation vanish. Hoisting the state to a provider mounted above
 * both makes expanding *just navigation*: no serialising through sessionStorage,
 * no rehydration step to get wrong.
 *
 * It is also why the conversation survives clicking between /compare and
 * /urunler with the popup open -- the provider sits in the (app) layout, which
 * does not remount across routes inside it.
 */

type ChatContextValue = {
  messages: AgentMessage[];
  status: ChatStatus;
  send: (text: string) => void;
  stop: () => void;
  newChat: () => void;
  /** Whether the popup is showing. Lives here so the expand button can close it. */
  popupOpen: boolean;
  setPopupOpen: (open: boolean) => void;
  think: boolean;
  setThink: (on: boolean) => void;
  /** Files staged for the next message, shared by both surfaces. */
  attachments: ReturnType<typeof useAttachments>;
};

const ChatContext = createContext<ChatContextValue | null>(null);

export function useChat(): ChatContextValue {
  const value = useContext(ChatContext);
  if (!value) {
    throw new Error("useChat must be used inside <ChatProvider>");
  }
  return value;
}

/**
 * Ids for messages.
 *
 * A counter, not `crypto.randomUUID()` or `Date.now()`: these ids are React keys
 * for a list rendered on both server and client, and anything non-deterministic
 * in that position is a hydration mismatch waiting to happen.
 */
let messageSeq = 0;
const nextId = (prefix: string) => `${prefix}-${++messageSeq}`;

export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [popupOpen, setPopupOpen] = useState(false);
  const [think, setThink] = useState(false);

  // Staged here rather than in the composer so a file picked in the popup is
  // still attached after expanding to the full page -- the same reason the
  // messages live up here.
  const attachments = useAttachments();

  // Held in a ref rather than state: aborting must not wait for a re-render, and
  // nothing renders differently based on the controller's identity.
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus("ready");
  }, []);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      // A second send while one is in flight cancels the first. Two concurrent
      // streams would interleave their deltas into whichever assistant message
      // happened to be last.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const userMessage: AgentMessage = {
        id: nextId("user"),
        role: "user",
        parts: [{ type: "text", text: trimmed }],
      };
      const assistantId = nextId("assistant");

      // The assistant's message is appended empty, up front. The alternative --
      // waiting for the first delta to create it -- means the list has nothing to
      // scroll to and nothing to attach the "thinking" state to during
      // `submitted`.
      setMessages((prev) => [
        ...prev,
        userMessage,
        { id: assistantId, role: "assistant", parts: [{ type: "text", text: "" }] },
      ]);
      setStatus("submitted");

      // The request carries the conversation *including* the new user turn, which
      // `messages` does not yet -- the setState above has not landed. Building it
      // here rather than reading state back is what keeps the agent from being
      // asked the previous question.
      const history = [...messages, userMessage];

      // Snapshotted before clearing, so the request describes what the user
      // actually attached to this turn.
      const staged = attachments.targets;
      attachments.clear();

      void (async () => {
        let text = "";
        try {
          for await (const chunk of streamChat(
            { messages: history, think, attachments: staged },
            { signal: controller.signal },
          )) {
            if (controller.signal.aborted) return;

            if (chunk.type === "error") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        parts: [
                          { type: "error", title: chunk.title, message: chunk.message },
                        ],
                      }
                    : m,
                ),
              );
              return;
            }

            text += chunk.delta;
            setStatus("streaming");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, parts: [{ type: "text", text }] } : m,
              ),
            );
          }
        } catch (error) {
          // An abort is a user action, not a failure -- surfacing it as an error
          // bubble would punish them for pressing stop.
          if (controller.signal.aborted) return;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    parts: [
                      {
                        type: "error",
                        message: error instanceof Error ? error.message : String(error),
                      },
                    ],
                  }
                : m,
            ),
          );
        } finally {
          if (abortRef.current === controller) {
            abortRef.current = null;
            setStatus("ready");
          }
        }
      })();
    },
    [messages, think, attachments],
  );

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setStatus("ready");
    attachments.clear();
  }, [attachments]);

  const value = useMemo(
    () => ({
      messages,
      status,
      send,
      stop,
      newChat,
      popupOpen,
      setPopupOpen,
      think,
      setThink,
      attachments,
    }),
    [messages, status, send, stop, newChat, popupOpen, think, attachments],
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
