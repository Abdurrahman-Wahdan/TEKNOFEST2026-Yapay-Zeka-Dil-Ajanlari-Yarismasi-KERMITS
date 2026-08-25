"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import {
  chatStore,
  historyServerSnapshot,
  historySnapshot,
  newConversationId,
  newMessageId,
  subscribeHistory,
  titleFor,
  type StoredConversation,
} from "./store";
import { useQueryClient } from "@tanstack/react-query";

import { streamChat } from "./transport";
import {
  attachmentMentionTargets,
  conversationAttachments,
  mentionedAttachments,
  mergeReusableAttachments,
  type ReusableAttachment,
} from "./attachment-mentions";
import { api } from "@/lib/api";
import { usePathname } from "@/i18n/navigation";
import { useLocale } from "next-intl";

import { formatLocation } from "./page-locator";
import { toCapturePayloads } from "./capture";
import { runClientTool } from "./tools";
import type {
  AgentMessage,
  ChatStatus,
  ClientToolName,
  MessagePart,
  PageViewMode,
  ToolResult,
  WebCitation,
} from "./types";
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
  /** Persisted server conversation used when a kept table gets its context. */
  serverSessionId?: string;
  status: ChatStatus;
  /** A private agent's context-aware next user message. */
  recommendation?: string;
  send: (text: string) => void;
  stop: () => void;
  newChat: () => void;
  /** Whether the popup is showing. Lives here so the expand button can close it. */
  popupOpen: boolean;
  setPopupOpen: (open: boolean) => void;
  think: boolean;
  setThink: (on: boolean) => void;
  /** Whether the user wants web search enabled for upcoming turns. */
  webSearch: boolean;
  setWebSearch: (on: boolean) => void;
  /**
   * The model answering, as a key from `GET /api/models`. `undefined` means the
   * server's configured default -- the composer never has to know what that is.
   */
  model?: string;
  setModel: (key: string | undefined) => void;
  /** Files staged for the next message, shared by both surfaces. */
  attachments: ReturnType<typeof useAttachments>;
  /** Staged files plus reusable files from earlier turns in this conversation. */
  mentionTargets: ReturnType<typeof attachmentMentionTargets>;

  /** Past conversations, newest first. Empty until the store has been read. */
  history: StoredConversation[];
  /** Which conversation is on screen. */
  activeId: string;
  /** Load a past conversation into both surfaces. */
  openConversation: (id: string) => void;
  /** Forget one. If it is the open one, this starts a fresh chat. */
  deleteConversation: (id: string) => void;
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
 * How many rounds of client tool use one question may take.
 *
 * A backstop, not a budget: an agent that asks to look at the page, is told what
 * the page says, and asks again would otherwise never return.
 */
const MAX_TOOL_PASSES = 3;

export function ChatProvider({ children }: { children: ReactNode }) {
  // The locale-stripped path, for anything staged from the page the user is on.
  const pathname = usePathname();
  const locale = useLocale();
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  /**
   * Past conversations, read straight from the store.
   *
   * `useSyncExternalStore` rather than state seeded in a mount effect: the effect
   * version had to `setState` on mount, which is both a render-then-correct and
   * exactly what `react-hooks/set-state-in-effect` warns about. This subscribes
   * instead, so a write anywhere updates every reader.
   */
  const history = useSyncExternalStore(
    subscribeHistory,
    historySnapshot,
    historyServerSnapshot,
  );
  const [activeId, setActiveId] = useState<string>(() => newConversationId());
  const [serverSessionId, setServerSessionId] = useState<string | undefined>();
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [recommendation, setRecommendation] = useState<string | undefined>();
  const [popupOpen, setPopupOpen] = useState(false);
  const queryClient = useQueryClient();
  const [think, setThink] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  // Undefined, not a hardcoded "gemma": the default belongs to the server, and
  // pinning it here would silently override a change made in settings.
  const [model, setModel] = useState<string | undefined>();

  // Staged here rather than in the composer so a file picked in the popup is
  // still attached after expanding to the full page -- the same reason the
  // messages live up here.
  const attachments = useAttachments();

  const stagedReusableAttachments: ReusableAttachment[] = useMemo(
    () => [
      ...attachments.images.flatMap((item) =>
        item.status === "ready" && item.attachmentId
          ? [{ id: item.attachmentId, filename: item.filename, kind: "image" as const }]
          : [],
      ),
      ...attachments.files.flatMap((item) =>
        item.status === "ready" && item.attachmentId
          ? [
              {
                id: item.attachmentId,
                filename: item.filename,
                kind: item.kind,
                pageCount: item.pageCount,
              },
            ]
          : [],
      ),
    ],
    [attachments.files, attachments.images],
  );
  const mentionTargets = useMemo(() => {
    const reusable = mergeReusableAttachments(
      stagedReusableAttachments,
      conversationAttachments(messages),
    );
    const historical = attachmentMentionTargets(
      reusable.filter(
        (file) => !stagedReusableAttachments.some((staged) => staged.id === file.id),
      ),
    );
    return [...attachments.targets, ...historical];
  }, [attachments.targets, messages, stagedReusableAttachments]);

  // Held in a ref rather than state: aborting must not wait for a re-render, and
  // nothing renders differently based on the controller's identity.
  const abortRef = useRef<AbortController | null>(null);
  const recommendationAbortRef = useRef<AbortController | null>(null);

  /**
   * Ask the separate recommendation agent after a complete assistant turn.
   *
   * This intentionally does not sit in the chat stream. The banking answer is
   * never delayed by recommendation generation, and a failed recommendation is
   * a quiet missing affordance rather than a failed conversation.
   */
  useEffect(() => {
    const last = messages.at(-1);
    const hasAnswer =
      last?.role === "assistant" &&
      last.parts.some((part) => part.type === "text" && part.text.trim());
    if (status !== "ready" || !serverSessionId || !hasAnswer) return;

    recommendationAbortRef.current?.abort();
    const controller = new AbortController();
    recommendationAbortRef.current = controller;
    setRecommendation(undefined);

    void api
      .conversationRecommendation(
        serverSessionId,
        locale.startsWith("tr") ? "tr" : "en",
        controller.signal,
      )
      .then((result) => {
        if (!controller.signal.aborted) setRecommendation(result.text);
      })
      .catch(() => {
        // Recommendations are an enhancement. The conversation remains fully
        // usable when the local model is busy or this background call is stopped.
      });

    return () => controller.abort();
  }, [locale, messages, serverSessionId, status]);

  /**
   * Persist the open conversation whenever it changes.
   *
   * Keyed on the message list, so it saves as the answer streams rather than only
   * at the end -- a reload mid-answer keeps what had arrived. An empty
   * conversation is deliberately not saved: a "new chat" the user never typed into
   * would otherwise appear in the list as an untitled row.
   */
  const emptyTitle = "…";
  useEffect(() => {
    if (messages.length === 0) return;
    const conversation: StoredConversation = {
      id: activeId,
      serverSessionId,
      title: titleFor(messages, emptyTitle),
      messages,
      updatedAt: Date.now(),
    };
    // The store notifies its subscribers, so nothing has to be set here.
    chatStore.save(conversation);
  }, [messages, activeId, serverSessionId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus("ready");
  }, []);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      setRecommendation(undefined);

      // Snapshotted before anything else, because they decide whether there is a
      // message at all and they travel in two directions: into the user's own
      // turn, and onto the request. Split by kind -- files are metadata (there is
      // still no upload endpoint) while context *is* its content.
      const stagedContexts = attachments.contexts;
      const stagedCaptures = attachments.captures;
      const reusableFiles = mergeReusableAttachments(
        stagedReusableAttachments,
        conversationAttachments(messages),
      );
      const mentionedFiles = mentionedAttachments(trimmed, reusableFiles);
      const stagedFiles = [...attachments.prepared, ...mentionedFiles.map(({ id }) => ({ id }))]
        .filter((file, index, all) => all.findIndex((candidate) => candidate.id === file.id) === index);
      const stagedDisplayFiles = [
        ...attachments.images.map((item) => ({
          filename: item.filename,
          kind: "image" as const,
          attachmentId: item.attachmentId,
        })),
        ...attachments.files.map((item) => ({
          filename: item.filename,
          kind: item.kind,
          pageCount: item.pageCount,
          attachmentId: item.attachmentId,
        })),
      ];

      /**
       * The page outlines that arrived with a capture, as ordinary text context.
       *
       * Split out here rather than at the button, so the tray shows one chip for
       * one press while the request still carries both halves in the fields the
       * backend expects.
       */
      const outgoingContext = [
        ...stagedContexts,
        ...stagedCaptures.flatMap((capture) =>
          capture.outline
            ? [
                {
                  id: `${capture.id}-outline`,
                  kind: "page" as const,
                  label: capture.label,
                  body: capture.outline,
                  format: "markdown" as const,
                  location: { path: pathname },
                },
              ]
            : [],
        ),
      ];

      // Nothing typed and nothing attached is not a message. But an attachment
      // with no question is: "here is this table" followed by a look is how
      // people actually use it, and requiring a word first made that impossible.
      if (
        !trimmed &&
        stagedContexts.length === 0 &&
        stagedCaptures.length === 0 &&
        stagedFiles.length === 0
      ) return;

      // A second send while one is in flight cancels the first. Two concurrent
      // streams would interleave their deltas into whichever assistant message
      // happened to be last.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const userMessage: AgentMessage = {
        id: newMessageId("user"),
        role: "user",
        parts: [
          // Attachments first, above the question, as they sit in the composer.
          ...stagedContexts.map((context) => ({
            type: "context" as const,
            kind: context.kind,
            label: context.label,
            body: context.body,
            // Printed here, not structured: a message part is persisted to
            // localStorage and read back by the renderer, and the renderer only
            // ever shows this to a person.
            source: formatLocation(context.location),
          })),
          // Label only, never the bytes: this array is written to localStorage on
          // every streamed token, and a base64 image would blow the quota and
          // silently stop history saving.
          ...stagedCaptures.map((capture) => ({
            type: "context" as const,
            kind: "capture" as const,
            label: capture.label,
          })),
          ...stagedDisplayFiles.map((file) => ({
            type: "attachment" as const,
            ...file,
          })),
          // Omitted when empty rather than sent blank: an empty text part renders
          // as an empty bubble.
          ...(trimmed ? [{ type: "text" as const, text: trimmed }] : []),
        ],
      };
      const assistantId = newMessageId("assistant");

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

      // Cleared only now: everything this turn needed was snapshotted above, so
      // the request still describes what the user actually attached.
      attachments.clear();

      void (async () => {
        let text = "";
        /**
         * What the agent did before answering, kept apart from its prose.
         *
         * Every delta rewrites the assistant's parts, so a tool-use note appended
         * into that array would be wiped by the next token. Held here and
         * re-prepended on each write instead.
         *
         * Labels only -- never a tool's bytes. These parts are persisted to
         * localStorage on every token, and a base64 capture in there would blow
         * the quota and silently stop history saving.
         */
        const toolParts: MessagePart[] = [];
        const citedSources: WebCitation[] = [];
        const answerParts = (): MessagePart[] => [
          ...toolParts,
          { type: "text", text },
          ...(citedSources.length > 0
            ? [{ type: "citations" as const, sources: [...citedSources] }]
            : []),
        ];
        const render = (parts: MessagePart[]) =>
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, parts } : m)),
          );

        try {
          let toolResults: ToolResult[] | undefined;

          /**
           * One pass per round of tool use.
           *
           * "Look at my screen" cannot be answered on the server, so the agent
           * asks, we answer, and the request goes again carrying the answer. The
           * cap is a backstop: an agent that asks to look at the page every time
           * it is told what the page says would otherwise loop forever.
           */
          for (let pass = 0; pass < MAX_TOOL_PASSES; pass += 1) {
            const pending: {
              id: string;
              name: ClientToolName;
              mode?: PageViewMode;
            }[] = [];

            for await (const chunk of streamChat(
              {
                messages: history,
                sessionId: serverSessionId,
                onSessionId: setServerSessionId,
                think,
                webSearch,
                model,
                attachments: stagedFiles,
                // Omitted rather than sent empty, so the payload says something
                // only when there is something to say.
                // The staged text context, plus the page outline that travelled
                // on any capture -- one press of the eye stages a single chip
                // carrying both representations, and they leave on their own
                // fields.
                context: outgoingContext.length > 0 ? outgoingContext : undefined,
                // Split into media type and base64 here, so the backend can drop
                // each one straight into an image content block. A data URL
                // forwarded as text shows the model the string rather than the
                // picture, and bills for it. Anything unsplittable is dropped
                // rather than sent as a broken image.
                captures: stagedCaptures.length > 0 ? toCapturePayloads(stagedCaptures) : undefined,
                toolResults,
              },
              { signal: controller.signal },
            )) {
              if (controller.signal.aborted) return;

              if (chunk.type === "error") {
                render([{ type: "error", title: chunk.title, message: chunk.message }]);
                return;
              }

              if (chunk.type === "tool-call") {
                // Collected, not run mid-stream: the agent may ask for several,
                // and running them as they arrive would serialise what can go
                // together.
                pending.push({ id: chunk.id, name: chunk.name, mode: chunk.mode });
                continue;
              }

              if (chunk.type === "citation") {
                // The same source can surface once from the nested web tool and
                // again from the specialist's evidence handoff. The API also
                // de-duplicates, but keeping this boundary idempotent protects
                // restored or proxied streams too.
                if (!citedSources.some(
                  (source) => source.url === chunk.citation.url
                    && source.sourceType === chunk.citation.sourceType,
                )) {
                  citedSources.push(chunk.citation);
                }
                render(answerParts());
                continue;
              }

              text += chunk.delta;
              setStatus("streaming");
              render(answerParts());
            }

            if (pending.length === 0) break;

            const results = await Promise.all(
              pending.map((call) => runClientTool(call.name, call.id, call.mode)),
            );
            if (controller.signal.aborted) return;

            for (const result of results) {
              toolParts.push({
                type: "context",
                kind: "capture",
                label: result.label,
              });
            }
            render(answerParts());
            toolResults = results;
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
            // The thread just grew, so the composer's context ring is stale.
            // Invalidated on completion rather than polled: the level only
            // moves when a turn does, and a timer would ask the agent for its
            // state on a loop while nothing is happening.
            queryClient.invalidateQueries({ queryKey: ["contextLevel"] });
          }
        }
      })();
    },
    [
      messages,
      serverSessionId,
      think,
      webSearch,
      model,
      attachments,
      pathname,
      queryClient,
      stagedReusableAttachments,
    ],
  );

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    // A new id, not a cleared list: the conversation being left is already in the
    // store under its own id, and reusing the id would overwrite it on the next
    // message.
    setActiveId(newConversationId());
    setServerSessionId(undefined);
    setMessages([]);
    setRecommendation(undefined);
    setStatus("ready");
    attachments.clear();
  }, [attachments]);

  const openConversation = useCallback(
    (id: string) => {
      const found = chatStore.list().find((c) => c.id === id);
      if (!found) return;
      // Whatever was streaming belongs to the conversation being left.
      abortRef.current?.abort();
      abortRef.current = null;
      setActiveId(found.id);
      setServerSessionId(found.serverSessionId);
      setMessages(found.messages);
      setRecommendation(undefined);
      setStatus("ready");
      attachments.clear();
    },
    [attachments],
  );

  const deleteConversation = useCallback(
    (id: string) => {
      const found = chatStore.list().find((conversation) => conversation.id === id);
      chatStore.remove(id);
      // The server session contains the agents' private checkpoint state as well
      // as visible messages, so deleting a UI conversation must remove both.
      if (found?.serverSessionId) {
        void api.deleteChatSession(found.serverSessionId).catch(() => {
          // Local deletion remains useful if the user is offline or logged out.
        });
      }
      // Deleting the conversation you are reading leaves nothing to read, so it
      // becomes a fresh one rather than an orphaned transcript with no store entry.
      if (id === activeId) {
        abortRef.current?.abort();
        abortRef.current = null;
        setActiveId(newConversationId());
        setServerSessionId(undefined);
        setMessages([]);
        setRecommendation(undefined);
        setStatus("ready");
        attachments.clear();
      }
    },
    [activeId, attachments],
  );

  const value = useMemo(
    () => ({
      messages,
      serverSessionId,
      status,
      recommendation,
      send,
      stop,
      newChat,
      popupOpen,
      setPopupOpen,
      think,
      setThink,
      webSearch,
      setWebSearch,
      model,
      setModel,
      attachments,
      mentionTargets,
      history,
      activeId,
      openConversation,
      deleteConversation,
    }),
    [
      messages,
      serverSessionId,
      status,
      recommendation,
      send,
      stop,
      newChat,
      popupOpen,
      think,
      webSearch,
      model,
      attachments,
      mentionTargets,
      history,
      activeId,
      openConversation,
      deleteConversation,
    ],
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
