"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  newConversationId,
  newMessageId,
  titleFor,
  toAgentMessages,
  toConversations,
  type Conversation,
} from "./store";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { streamChat } from "./transport";
import {
  attachmentMentionTargets,
  conversationAttachments,
  mentionedAttachments,
  mergeReusableAttachments,
  type ReusableAttachment,
} from "./attachment-mentions";
import { api } from "@/lib/api";
import { AUTOMATIONS_KEY } from "@/lib/automations";
import { useAuth } from "@/lib/auth";
import { usePathname } from "@/i18n/navigation";
import { useLocale } from "next-intl";

import { formatLocation } from "./page-locator";
import { toCapturePayloads } from "./capture";
import { runClientTool } from "./tools";
import type {
  AgentMessage,
  AttachedContext,
  CapturePayload,
  ChatStage,
  ChatStatus,
  ClientToolName,
  MessagePart,
  MessageFeedback,
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
  /**
   * What the agent is doing, while it is doing it.
   *
   * Undefined once the turn ends, and undefined for the whole turn if the server
   * sends no stage -- the transcript falls back to its generic label, which is
   * what it showed before this existed.
   */
  stage?: ChatStage;
  /** A private agent's context-aware next user message. */
  recommendation?: string;
  send: (text: string) => void;
  /**
   * Ask the last question again, in place of the answer it got.
   *
   * The turn is replaced, not appended: the assistant's message is emptied and
   * rewritten, and the request carries `regenerate` so the server drops the
   * exchange from the transcript and rewinds the supervisor's checkpoint before
   * running it. Without that the model would be shown the same question twice
   * and would answer the second one as a follow-up.
   *
   * A no-op when `canRetry` is false; the button that calls it is hidden then.
   */
  retry: () => void;
  /**
   * Whether the last answer can be asked for again *faithfully*.
   *
   * False while a turn is in flight, and false when the question cannot be
   * reproduced exactly -- see `retryPayload`. A retry that quietly asked a
   * smaller question than the original would be worse than no retry at all.
   */
  canRetry: boolean;
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

  /**
   * This account's conversations, newest first.
   *
   * From the server, not the browser -- the same list in every browser the user
   * signs into. Empty on the first render while the request is in flight, and
   * empty for a signed-out visitor, which are the same thing as far as the menu
   * is concerned.
   */
  history: Conversation[];
  /** Which conversation is on screen. The server session id, once there is one. */
  activeId: string;
  /** Load a past conversation into both surfaces. Fetches its turns. */
  openConversation: (id: string) => void;
  /** Delete one, on the server. If it is the open one, this starts a fresh chat. */
  deleteConversation: (id: string) => void;
  saveFeedback: (
    messageId: string,
    rating: MessageFeedback["rating"],
    note: string,
  ) => Promise<void>;
};

/**
 * The conversation list's cache key.
 *
 * Exported because it is invalidated from more than one place -- a turn
 * finishing, a deletion, and (once there is a second surface that writes
 * conversations) whatever comes next. A key written out twice is a key that
 * eventually disagrees with itself and leaves a stale sidebar.
 */
export const CHAT_SESSIONS_KEY = ["chat", "sessions"] as const;

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

/**
 * Everything a turn carries besides the words.
 *
 * Named because it is now passed between two functions instead of being built
 * and used in one, and because it is the unit "try again" has to reproduce: a
 * retry is the same payload against a possibly different model, which is the
 * whole reason the field is not folded into `AgentMessage`.
 *
 * Already in wire shape -- `captures` split into media type and base64, files
 * reduced to their opaque ids -- so replaying it needs no re-serialisation and
 * cannot re-serialise it differently the second time.
 */
type TurnPayload = {
  attachments: { id: string }[];
  context?: AttachedContext[];
  captures?: CapturePayload[];
};

/** A turn as it was sent, kept so it can be sent again. */
type ReplayableTurn = {
  assistantId: string;
  history: AgentMessage[];
  payload: TurnPayload;
};

export function ChatProvider({ children }: { children: ReactNode }) {
  // The locale-stripped path, for anything staged from the page the user is on.
  const pathname = usePathname();
  const locale = useLocale();
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const { user } = useAuth();
  /**
   * This account's conversations, from the server.
   *
   * Gated on a signed-in user rather than left to fail: `/chat/sessions` is
   * authenticated, and an unauthenticated fetch on every visit to a public page
   * would be a 401 in the console with nothing to show for it.
   *
   * No `refetchInterval`. A conversation list only changes when this browser
   * changes it -- there is no second writer -- so it is invalidated at the two
   * moments that can move it (a turn finishing, a deletion) rather than polled.
   */
  const sessions = useQuery({
    queryKey: CHAT_SESSIONS_KEY,
    queryFn: () => api.chatSessions(),
    enabled: Boolean(user),
  });
  const [activeId, setActiveId] = useState<string>(() => newConversationId());
  const [serverSessionId, setServerSessionId] = useState<string | undefined>();
  const [status, setStatus] = useState<ChatStatus>("ready");
  // Cleared rather than left on the last stage when a turn ends, so a finished
  // answer never sits under "Yanıt denetleniyor…".
  const [stage, setStage] = useState<ChatStage | undefined>(undefined);
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
   * The turn that produced the answer currently on screen.
   *
   * A ref, not state: nothing renders from it directly -- `retryable` reads it
   * while deriving from `messages`, which is what re-renders -- and it must be
   * written during `send` without scheduling a render of its own.
   *
   * Not cleared when the conversation changes. It does not need to be: it is
   * only ever used after matching `assistantId` against the message actually on
   * screen, and message ids are unique across conversations. Clearing it in the
   * three places a conversation can change would be three places to forget.
   */
  const lastTurnRef = useRef<ReplayableTurn | null>(null);

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
   * The history menu's list: this account's conversations, plus the one being
   * written right now.
   *
   * The extra row is not decoration. A session id does not reach the browser
   * until the `done` frame, so for the whole minute an answer takes to arrive the
   * server list cannot contain the conversation the user is looking at -- and
   * opening the menu mid-answer would show every conversation except that one,
   * with nothing highlighted. Named with `titleFor`, which derives the same title
   * from the same turn that `_title_for` will derive on the server, so the row is
   * not renamed underneath the reader when the answer lands.
   *
   * Dropped as soon as the real row exists, matched by id rather than by
   * position: `onSessionId` adopts the server id as `activeId`, so from that
   * moment the two are the same row and only one may render.
   */
  const history = useMemo<Conversation[]>(() => {
    const stored = toConversations(sessions.data ?? []);
    if (messages.length === 0 || stored.some((row) => row.id === activeId)) {
      return stored;
    }
    return [
      { id: activeId, title: titleFor(messages, "…"), updatedAt: Date.now() },
      ...stored,
    ];
  }, [sessions.data, messages, activeId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus("ready");
    setStage(undefined);
  }, []);

  /**
   * Take the server's id for this conversation as its identity.
   *
   * Called from the `done` frame of the first turn, which is the first moment the
   * browser learns it. Two things happen here and both matter:
   *
   * `activeId` becomes the session id, so the conversation the user is reading is
   * the same row the history menu lists -- otherwise the menu would highlight
   * nothing, and clicking the row for the open conversation would re-fetch it as
   * though it were a different one.
   *
   * The list is invalidated, so a conversation that has just come into existence
   * appears in every other tab too. `updated_at` moved on a later turn as well,
   * which is what reorders the menu, so this is not only for the first one.
   *
   * That invalidation reaches the *transcripts* as well, and it has to. A
   * conversation's turns are cached under `[...CHAT_SESSIONS_KEY, id]`, and
   * react-query matches a key as a prefix -- so invalidating the list marks
   * every cached transcript stale too. Without it, leaving a conversation right
   * after answering and coming back inside the 60s `staleTime` would reopen it
   * one turn short.
   */
  const adoptSessionId = useCallback(
    (id: string) => {
      setServerSessionId(id);
      setActiveId(id);
      void queryClient.invalidateQueries({ queryKey: CHAT_SESSIONS_KEY });
    },
    [queryClient],
  );

  /**
   * Run one exchange against the agent and write it into `assistantId`.
   *
   * Extracted from `send` so that "ask this" and "ask this again" are the same
   * code path. The two differ in exactly three things -- who builds the user's
   * message, whether the assistant's bubble is new or being rewritten, and the
   * `regenerate` flag -- and every hard part below (the tool-use loop, the
   * abort semantics, which citations are duplicates, what a stopped stream must
   * not do) is common to both. A second copy of it would drift.
   *
   * `think`, `webSearch` and `model` are read from state at call time rather
   * than captured with the turn. That is deliberate and it is most of the point
   * of the retry button: the user switches model in the Advanced menu and asks
   * again, and the answer has to come from the model they just chose.
   */
  const runTurn = useCallback(
    (
      history: AgentMessage[],
      assistantId: string,
      payload: TurnPayload,
      { regenerate = false }: { regenerate?: boolean } = {},
    ) => {
      // A second run while one is in flight cancels the first. Two concurrent
      // streams would interleave their deltas into whichever assistant message
      // happened to be last.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus("submitted");
      setStage(undefined);

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
                onSessionId: adoptSessionId,
                think,
                webSearch,
                model,
                // Already in wire shape when it reached this function: the files
                // as opaque ids, the staged text context plus the page outline
                // that travelled on any capture, and each capture split into
                // media type and base64.
                ...payload,
                /*
                  Only the first pass regenerates. A client tool suspends the turn
                  and the request goes again, and a second `regenerate` on that
                  pass would delete the very turn the first pass had just written
                  -- rewinding one exchange further back each time the agent asked
                  to look at the page.
                */
                regenerate: regenerate && pass === 0,
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

              if (chunk.type === "automation") {
                // The agent wrote to the user's standing orders. Refetch the
                // list, so a profile page open in this or any other tab stops
                // showing a version of it that predates the write.
                //
                // Invalidated rather than optimistically appended: the row's
                // real title, schedule and next-run time come from the server,
                // and this frame deliberately carries none of them.
                queryClient.invalidateQueries({ queryKey: AUTOMATIONS_KEY });
                continue;
              }

              if (chunk.type === "status") {
                // A label for the spinner and nothing more. The stages are not
                // monotonic -- a turn the output check hands back returns to
                // `pricing` -- so this is a plain assignment, not a ratchet.
                setStage(chunk.stage);
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

              if (chunk.type === "done") {
                setMessages((prev) => prev.map((message) =>
                  message.id === assistantId
                    ? { ...message, id: chunk.messageId }
                    : message
                ));
                if (lastTurnRef.current?.assistantId === assistantId) {
                  lastTurnRef.current = {
                    ...lastTurnRef.current,
                    assistantId: chunk.messageId,
                  };
                }
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
            setStage(undefined);
            // The thread just grew, so the composer's context ring is stale.
            // Invalidated on completion rather than polled: the level only
            // moves when a turn does, and a timer would ask the agent for its
            // state on a loop while nothing is happening.
            queryClient.invalidateQueries({ queryKey: ["contextLevel"] });
          }
        }
      })();
    },
    [adoptSessionId, serverSessionId, think, webSearch, model, queryClient],
  );

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

      // The request carries the conversation *including* the new user turn, which
      // `messages` does not yet -- the setState above has not landed. Building it
      // here rather than reading state back is what keeps the agent from being
      // asked the previous question.
      const history = [...messages, userMessage];

      // Cleared only now: everything this turn needed was snapshotted above, so
      // the request still describes what the user actually attached.
      attachments.clear();

      const payload: TurnPayload = {
        attachments: stagedFiles,
        // Omitted rather than sent empty, so the payload says something only when
        // there is something to say.
        context: outgoingContext.length > 0 ? outgoingContext : undefined,
        // Split into media type and base64 here, so the backend can drop each one
        // straight into an image content block. A data URL forwarded as text shows
        // the model the string rather than the picture, and bills for it. Anything
        // unsplittable is dropped rather than sent as a broken image.
        captures: stagedCaptures.length > 0 ? toCapturePayloads(stagedCaptures) : undefined,
      };
      /*
        Kept so that "try again" re-sends *this* turn rather than a reconstruction
        of it. The transcript is a lossy record of a request on purpose -- a
        capture's bytes are deliberately never persisted -- so rebuilding the
        payload from the messages would quietly ask a smaller question than the
        one that was asked. `retryable` falls back to that reconstruction only for
        turns this ref cannot describe, and refuses when the loss would matter.
      */
      lastTurnRef.current = { assistantId, history, payload };
      runTurn(history, assistantId, payload);
    },
    [attachments, messages, pathname, runTurn, stagedReusableAttachments],
  );

  /**
   * The last exchange, ready to be run again -- or `null` when it must not be.
   *
   * Refusing is a real outcome here, not a defensive shrug. A retry that dropped
   * the table the question was about would answer a *different, smaller*
   * question and present it as a second attempt at the first one, which is worse
   * than having no button. So the turn is retried from `lastTurnRef` when that
   * describes it, reconstructed from the transcript when the parts carry
   * everything (a plain question does, and that is the overwhelming majority),
   * and refused when they do not.
   *
   * What the transcript cannot give back: a page capture, whose bytes are never
   * persisted, and a context part or file whose body or id has been dropped.
   */
  const retryable = useMemo(() => {
    // Not while one is in flight. `stop` then retry is available and unambiguous;
    // a retry that raced the stream it was replacing would not be.
    if (status === "submitted" || status === "streaming") return null;

    const assistantIndex = messages.length - 1;
    const assistant = messages[assistantIndex];
    if (!assistant || assistant.role !== "assistant") return null;

    let userIndex = assistantIndex - 1;
    while (userIndex >= 0 && messages[userIndex].role !== "user") userIndex -= 1;
    if (userIndex < 0) return null;

    const cached = lastTurnRef.current;
    if (cached && cached.assistantId === assistant.id) return cached;

    // Restored from the server, or from an earlier session of this browser.
    const user = messages[userIndex];
    const reproducible = user.parts.every(
      (part) =>
        (part.type !== "context" || (part.kind !== "capture" && Boolean(part.body))) &&
        (part.type !== "attachment" || Boolean(part.attachmentId)),
    );
    if (!reproducible) return null;

    const context = user.parts.flatMap<AttachedContext>((part, index) =>
      part.type === "context" && part.body
        ? [
            {
              id: `${user.id}-${index}`,
              // Narrowed by `reproducible` above; the ternary is what tells the
              // compiler so, since `ContextKind` has no `capture` member.
              kind: part.kind === "capture" ? "page" : part.kind,
              label: part.label,
              body: part.body,
              // The original `format` and `location` are not persisted -- the
              // transcript keeps a printed `source` line, which is for a reader.
              // Both only frame the body for the agent; the body itself, which is
              // what the answer depends on, is exact.
              format: "markdown",
              location: { path: pathname },
            },
          ]
        : [],
    );

    return {
      assistantId: assistant.id,
      history: messages.slice(0, assistantIndex),
      payload: {
        attachments: user.parts.flatMap((part) =>
          part.type === "attachment" && part.attachmentId ? [{ id: part.attachmentId }] : [],
        ),
        context: context.length > 0 ? context : undefined,
      },
    };
  }, [messages, pathname, status]);

  const retry = useCallback(() => {
    const turn = retryable;
    if (!turn) return;
    setRecommendation(undefined);
    /*
      The answer is emptied and rewritten in place rather than removed and
      re-appended. Keeping the message id keeps the list's keys stable, so the
      transcript does not jump under the reader at the moment they pressed a
      button next to it -- and it is what makes a second retry find the same turn.
    */
    setMessages((prev) =>
      prev.map((message) =>
        message.id === turn.assistantId
          ? { ...message, parts: [{ type: "text", text: "" }] }
          : message,
      ),
    );
    lastTurnRef.current = turn;
    runTurn(turn.history, turn.assistantId, turn.payload, { regenerate: true });
  }, [retryable, runTurn]);

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
    setStage(undefined);
    attachments.clear();
  }, [attachments]);

  /**
   * Load a past conversation into both surfaces.
   *
   * Its turns are fetched rather than held in the list: the menu needs a title
   * and the transcript needs everything, and keeping every message of fifty
   * conversations in memory to render a list of fifty strings was the shape the
   * localStorage store had. One request, cached by react-query, on the click that
   * actually needs it.
   *
   * The transcript is cleared before the fetch, not after. It takes a moment, and
   * leaving the previous conversation on screen while a different row is
   * highlighted reads as the click having failed -- so the surfaces show the
   * conversation being opened, empty, rather than the one being left.
   *
   * A failed fetch leaves an empty transcript on a real conversation. It is the
   * honest outcome: the alternative is restoring the previous conversation under
   * the new title, and there is nothing to put in its place. The row stays in the
   * menu, so the retry is one more click.
   */
  const openConversation = useCallback(
    (id: string) => {
      if (id === activeId) return;
      // Whatever was streaming belongs to the conversation being left.
      abortRef.current?.abort();
      abortRef.current = null;
      setActiveId(id);
      setServerSessionId(id);
      setMessages([]);
      setRecommendation(undefined);
      setStatus("ready");
      setStage(undefined);
      attachments.clear();

      void queryClient
        .fetchQuery({
          queryKey: [...CHAT_SESSIONS_KEY, id],
          queryFn: () => api.chatSession(id),
        })
        .then((detail) => setMessages(toAgentMessages(detail)))
        .catch(() => {
          // Nothing to show and nothing to substitute. See above.
        });
    },
    [activeId, attachments, queryClient],
  );

  /**
   * Delete a conversation, on the server, which is now the only copy.
   *
   * The session row carries the agents' private checkpoint state as well as the
   * visible turns, so this is what makes a deletion mean anything -- deleting
   * only the transcript would leave the supervisor still remembering the
   * conversation on its next turn.
   *
   * The list is invalidated in `finally`: a failed delete has to put the row
   * back, because the alternative is a conversation the user believes is gone
   * and which reappears on the next reload.
   */
  const deleteConversation = useCallback(
    (id: string) => {
      // The in-flight conversation has no server row yet, so there is nothing to
      // delete -- but it is a real row in the menu, and leaving it there after
      // the user asked for it to go would be the click doing nothing.
      const onServer = id !== activeId || serverSessionId !== undefined;
      if (onServer) {
        void api
          .deleteChatSession(id)
          .catch(() => {
            // Reported by the row coming back below, rather than by a toast this
            // menu has no room for.
          })
          .finally(() => {
            void queryClient.invalidateQueries({ queryKey: CHAT_SESSIONS_KEY });
          });
      }
      // Deleting the conversation you are reading leaves nothing to read, so it
      // becomes a fresh one rather than a transcript with no row behind it.
      if (id === activeId) {
        abortRef.current?.abort();
        abortRef.current = null;
        setActiveId(newConversationId());
        setServerSessionId(undefined);
        setMessages([]);
        setRecommendation(undefined);
        setStatus("ready");
        setStage(undefined);
        attachments.clear();
      }
    },
    [activeId, attachments, queryClient, serverSessionId],
  );

  const saveFeedback = useCallback(
    async (messageId: string, rating: MessageFeedback["rating"], note: string) => {
      if (!serverSessionId) {
        throw new Error("This answer has not been saved yet.");
      }
      const saved = await api.saveMessageFeedback(serverSessionId, messageId, {
        rating,
        note,
      });
      setMessages((prev) => prev.map((message) =>
        message.id === messageId
          ? {
              ...message,
              feedback: {
                id: saved.id,
                messageId: saved.message_id,
                rating: saved.rating,
                note: saved.note,
                createdAt: saved.created_at,
                updatedAt: saved.updated_at,
              },
            }
          : message
      ));
      await queryClient.invalidateQueries({
        queryKey: ["chat", "session", serverSessionId],
      });
    },
    [queryClient, serverSessionId],
  );

  const value = useMemo(
    () => ({
      messages,
      serverSessionId,
      status,
      stage,
      recommendation,
      send,
      retry,
      canRetry: retryable !== null,
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
      saveFeedback,
    }),
    [
      messages,
      serverSessionId,
      status,
      stage,
      recommendation,
      send,
      retry,
      retryable,
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
      saveFeedback,
    ],
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
