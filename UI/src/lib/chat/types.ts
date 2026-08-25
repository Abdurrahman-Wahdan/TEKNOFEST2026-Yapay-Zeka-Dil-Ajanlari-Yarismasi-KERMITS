/**
 * The shapes the chat surfaces agree on.
 *
 * Deliberately modelled on the AI SDK's `UIMessage` convention -- a message is a
 * list of *parts* rather than a string. The backend agent is not written yet, and
 * every agent protocol worth speaking to emits more than prose: tool calls,
 * citations, reasoning traces. A `string` body would have to be replaced to carry
 * any of those; a `parts[]` absorbs them as new members of the union.
 *
 * Only `text` and `error` are handled today. Adding a part type means adding a
 * case in `ChatMessage`, and nothing else.
 */

/**
 * Where the conversation is.
 *
 * `submitted` is the gap between sending and the first token arriving -- the
 * composer must already show a stop button there, because that wait is exactly
 * when a user wants to cancel. Folding it into `streaming` is why that button
 * used to appear late.
 */
export type ChatStatus = "ready" | "submitted" | "streaming" | "idle";

/** A claim-used source proven by an actual bank-specialist tool call. */
export type WebCitation = {
  url: string;
  title?: string;
  bank?: string;
  sourceType?: string;
};

export type MessagePart =
  | { type: "text"; text: string }
  | { type: "error"; title?: string; message: string }
  | { type: "citations"; sources: WebCitation[] }
  | {
      type: "attachment";
      filename: string;
      kind: "image" | "text" | "document";
      pageCount?: number;
      /**
       * Opaque, owner-bound handle returned by the preparation endpoint.
       *
       * Keeping the handle beside the visible label makes a file mention usable
       * on a later turn. The bytes remain server-side and the handle expires;
       * it is not model content and the renderer never exposes it.
       */
      attachmentId?: string;
    }
  /**
   * A piece of the app the user handed to the agent: a quote, a table row, a
   * whole table, a page capture.
   *
   * `body` is optional, and its absence is load-bearing. The provider persists
   * `messages` to localStorage on every streamed token, and a failed write is
   * swallowed -- so a base64 capture in here would quietly stop history saving
   * once the quota went. Text bodies are capped and worth keeping; image bytes
   * live only on the request, and the transcript keeps the label alone.
   */
  | {
      type: "context";
      kind: ContextKind | "capture";
      label: string;
      body?: string;
      source?: string;
    };

export type AgentMessage = {
  id: string;
  role: "user" | "assistant";
  parts: MessagePart[];
};

import type { ContextLocation } from "./page-locator";

/**
 * What kind of thing was lifted out of the UI.
 *
 * `chart` exists with nothing yet producing it: no chart is rendered anywhere in
 * the live app (`recharts` is installed and unimported; the ApexCharts widgets
 * belong to the unmounted template layouts). It is here so the seam is real when
 * a chart widget arrives, not as a promise that something already works.
 */
export type ContextKind = "quote" | "row" | "table" | "chart" | "page";

/**
 * A piece of the UI, staged for the next message.
 *
 * Serialised at the moment it is attached rather than at send time. What the
 * user pointed at is what should travel -- the table may have been re-sorted,
 * re-filtered or navigated away from by the time they finish typing.
 */
export type AttachedContext = {
  id: string;
  kind: ContextKind;
  /** The chip's text, and the name the `@` menu offers. */
  label: string;
  /** The LLM-facing body, already serialised. */
  body: string;
  format: "markdown" | "markdown-kv";
  /**
   * Where on the page it came from -- page, section, table, row, column.
   *
   * Structured rather than a printed breadcrumb because the agent parses
   * attributes far better than it parses prose, and because the chip needs only
   * the most specific part while the request wants all of it. A bare pathname was
   * the first version and it threw away most of the meaning: "from /urunler" left
   * the agent guessing which table and which row.
   */
  location: ContextLocation;
  /** How many records the body holds, for the chip's subline. */
  count?: number;
};

/**
 * A picture of the page the user took, staged for the next message.
 *
 * Its own kind rather than an `AttachedImage`, because its lifecycle is different
 * in the one way that matters: the bytes must never be written to localStorage.
 * The provider persists `messages` on every streamed token and swallows a quota
 * failure, so a base64 capture in the transcript would silently stop history
 * saving. It travels on the request and nowhere else; the transcript keeps a label.
 *
 * The `dataUrl` doubles as the thumbnail's `src`, which is why there is no object
 * URL here and nothing to revoke.
 */
export type AttachedCapture = {
  id: string;
  label: string;
  dataUrl: string;
  width: number;
  height: number;
  bytes: number;
  /**
   * The page's text outline, when the user asked the assistant to look and we
   * captured both representations.
   *
   * Carried on the capture rather than staged as a second chip: one press of "let
   * the assistant see this page" is one thing the user did, and showing them two
   * attachments would expose exactly the mechanism the eye is there to hide. The
   * provider splits it back out at send time -- the picture onto `captures`, this
   * onto `context`.
   */
  outline?: string;
};

export type AttachedImage = {
  id: string;
  filename: string;
  url: string;
  size?: number;
  attachmentId?: string;
  status: "uploading" | "ready" | "error";
  error?: string;
};

export type AttachedFile = {
  id: string;
  filename: string;
  size?: number;
  attachmentId?: string;
  kind: "text" | "document";
  pageCount?: number;
  status: "uploading" | "ready" | "error";
  error?: string;
};

/**
 * The staged attachments, and what the composer can do to them.
 *
 * Files are prepared immediately by the authenticated API. Document page images
 * remain server-side and only their opaque ids travel with the chat request.
 */
export type ChatAttachments = {
  onAttach?: () => void;
  images?: AttachedImage[];
  files?: AttachedFile[];
  contexts?: AttachedContext[];
  captures?: AttachedCapture[];
  onRemoveImage?: (id: string) => void;
  onRemoveFile?: (id: string) => void;
  onRemoveContext?: (id: string) => void;
  onRemoveCapture?: (id: string) => void;
  hasPending?: boolean;
  hasError?: boolean;
};

/** Everything staged, flattened, for the `@` menu to list. */
export type MentionTarget = {
  id: string;
  filename: string;
  kind: "image" | "file" | "context";
  /**
   * Which kind of context, when `kind` is `"context"`.
   *
   * The menu lists a target by its label, and a label like "Kuveyt Türk" does not
   * say whether it is a quote, a row or a table -- so the glyph has to, and the
   * flattened target is all the menu gets.
   */
  contextKind?: ContextKind;
};

/** What the UI asks the agent for. */
export type ChatRequest = {
  messages: AgentMessage[];
  /**
   * The persisted FastAPI conversation behind this browser-local transcript.
   *
   * Omitted for the first turn; the API creates a session and returns its id in
   * the final SSE frame. It is intentionally not an `AgentMessage` field: the
   * id is transport state, never model context.
   */
  sessionId?: string;
  /** Called when the API creates the server-side session on the first turn. */
  onSessionId?: (sessionId: string) => void;
  /**
   * The Advanced menu's thinking switch: keep chain-of-thought on.
   *
   * Only models that reason by default are affected -- `supports_thinking` on
   * `GET /api/models` says which, and the menu disables the switch for the rest
   * rather than offering a toggle that changes nothing.
   */
  think?: boolean;
  /** Permit each delegated bank specialist to research its own public domain. */
  webSearch?: boolean;
  /**
   * The Advanced menu's model choice: a key from `GET /api/models`.
   *
   * Undefined answers with the server's configured chat model. Per turn, not
   * per session: the conversation lives in the agent's checkpointer, so
   * switching mid-thread keeps the history already built up.
   */
  model?: string | null;
  /** Opaque ids of server-prepared files staged for this turn. */
  attachments?: { id: string }[];
  /**
   * Pieces of the UI the user attached -- rows, tables, quotes -- already
   * serialised. Unlike `attachments` this *is* the content, because it is text
   * and small enough to carry.
   */
  context?: AttachedContext[];
  /**
   * Page captures, as data URLs.
   *
   * Kept apart from `context` on purpose: a different lifecycle (never
   * persisted), a different size class, and a backend will want these as image
   * content blocks rather than as prose.
   */
  captures?: CapturePayload[];
  /**
   * Answers to tool calls the agent made on the previous pass.
   *
   * Kept off `messages` deliberately. The provider persists `messages` to
   * localStorage on every streamed token and swallows a quota failure, so a
   * base64 capture in there would quietly stop history saving; and a tool result
   * is scoped to the exchange that asked for it, not to the conversation.
   */
  toolResults?: ToolResult[];
};

/**
 * One slice off the wire.
 *
 * `text-delta` carries an *append*, not the whole answer so far. The renderer
 * accumulates, which is what lets a 4kB answer stream without re-sending 4kB on
 * every tick.
 */
export type ChatChunk =
  | { type: "text-delta"; delta: string }
  | { type: "citation"; citation: WebCitation }
  | { type: "error"; message: string; title?: string }
  /**
   * The agent asking the *client* to do something only the client can.
   *
   * "Look at my screen" cannot be served from the server: the page exists in the
   * browser and nowhere else. So the agent asks, the client answers, and the
   * request is re-issued with the answer attached -- the ordinary tool-use
   * round trip, with the tool running here.
   */
  | { type: "tool-call"; id: string; name: ClientToolName; mode?: PageViewMode };

/**
 * What the agent is allowed to ask the browser to do.
 *
 * A closed list, not a name the backend can choose freely: this runs client code
 * because something on the wire asked it to, so what is callable is decided here.
 */
export type ClientToolName = "look_at_page";

/**
 * How the agent wants to see the page.
 *
 * A parameter rather than three separate tools. Looking at the page is one
 * capability with two representations, and splitting it into `read_page` and
 * `capture_page` made the agent choose before it knew which it needed -- then pay
 * a whole extra round trip when it chose wrong.
 *
 * - `text`  the semantic outline: exact figures, current filter state, cheap.
 * - `image` a picture: for questions about layout rather than data.
 * - `both`  one round trip, everything. The right default when unsure.
 */
export type PageViewMode = "text" | "image" | "both";

/**
 * A page capture, ready to become an image content block.
 *
 * `mediaType` and `data` separately, not a `data:` URL, because that is the shape
 * every vision API takes -- and because a backend that forwarded a data URL as
 * text would show the model the string `data:image/webp;base64,...` instead of the
 * picture, at full token cost. See `splitDataUrl` in `capture.ts`.
 */
export type CapturePayload = {
  id: string;
  label: string;
  mediaType: string;
  /** base64, no `data:` prefix. */
  data: string;
  width: number;
  height: number;
};

/** What a client tool sent back. */
export type ToolResult = {
  id: string;
  name: ClientToolName;
  /** Text results -- the page outline. */
  text?: string;
  /** Image results, ready to be turned into an image content block. */
  image?: CapturePayload;
  /** Short human summary, for the transcript. */
  label: string;
};
