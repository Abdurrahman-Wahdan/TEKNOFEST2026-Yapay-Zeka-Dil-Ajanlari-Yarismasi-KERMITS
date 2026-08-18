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

export type MessagePart =
  | { type: "text"; text: string }
  | { type: "error"; title?: string; message: string };

export type AgentMessage = {
  id: string;
  role: "user" | "assistant";
  parts: MessagePart[];
};

export type AttachedImage = {
  id: string;
  filename: string;
  url: string;
  size?: number;
};

export type AttachedFile = {
  id: string;
  filename: string;
  size?: number;
};

/**
 * The staged attachments, and what the composer can do to them.
 *
 * `onAttach` opens the file picker. The bytes never leave the browser yet --
 * there is no upload endpoint -- so what is staged here is what the `@` mention
 * menu offers and what travels on the request as metadata.
 */
export type ChatAttachments = {
  onAttach?: () => void;
  images?: AttachedImage[];
  files?: AttachedFile[];
  onRemoveImage?: (id: string) => void;
  onRemoveFile?: (id: string) => void;
};

/** Everything staged, flattened, for the `@` menu to list. */
export type MentionTarget = {
  id: string;
  filename: string;
  kind: "image" | "file";
};

/** What the UI asks the agent for. */
export type ChatRequest = {
  messages: AgentMessage[];
  /** The composer's "Think" toggle -- ask for a longer reasoning pass. */
  think?: boolean;
  /**
   * Files the user staged for this turn.
   *
   * Metadata only. There is no upload endpoint yet, so the bytes stay in the
   * browser and this says what the agent should expect to be given.
   */
  attachments?: { id: string; filename: string; kind: "image" | "file" }[];
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
  | { type: "error"; message: string; title?: string };
