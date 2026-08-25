import type { ChatMessage, ChatSession, ChatSessionDetail } from "@/lib/api";

import type { AgentMessage, MessagePart } from "./types";

/**
 * Where past conversations are kept: the account.
 *
 * This module used to be a localStorage store, with a note at the top calling
 * itself "one interface, a local implementation now, and a one-file swap when
 * the backend lands". The backend landed -- `chat_sessions` and `chat_messages`
 * have been written on every turn for as long as there has been an `/ask`
 * endpoint -- and the swap was never made, so the sidebar kept reading the
 * browser while the server quietly accumulated the real history.
 *
 * The visible consequence was that one signed-in account had a different history
 * in every browser and none at all in a new one: sixty-six conversations on the
 * server, two in Chrome, none in Safari. A conversation belongs to whoever had
 * it, not to the machine they were sitting at.
 *
 * So there is no store here any more, only the mapping between what the API
 * returns and what the transcript renders. React reads the list through
 * react-query like every other server resource in the app; the two are not
 * interchangeable, and pretending they were is what let this drift for so long.
 *
 * **What is deliberately lost:** the old store saved on every streamed token, so
 * a reload during an answer kept the half of it that had arrived. The server does
 * not persist a partial answer -- `api/routers/chat.py` discards one on purpose,
 * because a half-written reply replayed later reads as a complete one -- so a
 * reload mid-answer now loses it. That is the same trade the API already made for
 * the model's own history, and it is better than two disagreeing transcripts.
 */

/** One row in the history menu. The transcript is fetched when it is opened. */
export type Conversation = {
  /** The server session id. This is the conversation's identity everywhere. */
  id: string;
  title: string;
  /** Epoch ms, for ordering the list newest-first. */
  updatedAt: number;
};

/**
 * A title from the first user message.
 *
 * The server derives the same thing from the same turn (`_title_for` in
 * `api/routers/chat.py`), and that is what the list shows. This is still here for
 * the one row the server cannot have yet: the conversation being streamed right
 * now. Its session id does not reach the browser until the `done` frame, so until
 * then the menu has to name it from what is on screen -- and a conversation the
 * user is looking at should not be missing from their own history.
 *
 * The two must agree, or the row would be renamed the moment the answer lands.
 * Both take the first user turn, unwrap `@[mentions]`, collapse whitespace and
 * cut at 48/60 characters.
 */
export function titleFor(messages: AgentMessage[], fallback: string): string {
  const first = messages.find((m) => m.role === "user");
  const text = first?.parts.find((p) => p.type === "text");
  let raw = text && text.type === "text" ? text.text.trim() : "";
  // A turn can be an attachment with no question -- "here is this table" and a
  // look. Naming that conversation "New chat" throws away the one thing that
  // would let the user find it again.
  if (!raw) {
    const context = first?.parts.find((p) => p.type === "context");
    if (context && context.type === "context") raw = context.label.trim();
  }
  if (!raw) return fallback;
  // Mentions are stored as `@[filename]`; the brackets are machinery, not prose.
  const clean = raw.replace(/@\[([^\]]+)\]/g, "$1").replace(/\s+/g, " ");
  return clean.length > 48 ? `${clean.slice(0, 48).trimEnd()}…` : clean;
}

/** A conversation id that survives a reload without colliding. */
export function newConversationId(): string {
  // `randomUUID` needs a secure context; the fallback keeps a plain-http dev
  // origin working rather than throwing on the first "new chat".
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `c-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

/** A client-created message key that cannot collide with restored history. */
export function newMessageId(role: AgentMessage["role"]): string {
  return `${role}-${newConversationId()}`;
}

/** The part kinds the renderer knows how to draw. */
const PART_TYPES = new Set(["text", "error", "citations", "attachment", "context"]);

/**
 * One stored part, if it is one this renderer understands.
 *
 * Shape-checked rather than cast. The generated type for this field is
 * `Record<string, unknown>[]` -- the part union lives in `types.ts` and is not
 * mirrored into the OpenAPI schema on purpose, because a second definition is a
 * second thing to keep in step. So the check happens here, once.
 *
 * A part with an unknown `type` is dropped rather than rendered: a client that
 * has not been deployed yet reading a part written by a newer server should show
 * the rest of the turn, not an empty transcript.
 */
function asPart(value: unknown): MessagePart | null {
  if (!value || typeof value !== "object") return null;
  const part = value as { type?: unknown };
  if (typeof part.type !== "string" || !PART_TYPES.has(part.type)) return null;
  return value as MessagePart;
}

/**
 * One stored turn as the transcript renders it.
 *
 * `parts` is the authority and `content` is the fallback, which is the same
 * order the server applies (`api/chat_parts.py::parts_or_text`) -- it is repeated
 * here because a message can also lose its parts on the way through, and a turn
 * with text in it must never render as an empty bubble.
 */
export function toAgentMessage(message: ChatMessage): AgentMessage {
  const parts = (message.parts ?? [])
    .map(asPart)
    .filter((part): part is MessagePart => part !== null);
  return {
    // The database id, so a re-fetch of the same conversation produces the same
    // React keys and the transcript is not remounted under the reader.
    id: message.id,
    role: message.role,
    parts: parts.length > 0 ? parts : [{ type: "text", text: message.content }],
  };
}

/** A fetched conversation, ready to load into both chat surfaces. */
export function toAgentMessages(detail: ChatSessionDetail): AgentMessage[] {
  return (detail.messages ?? []).map(toAgentMessage);
}

/**
 * The history list, newest first.
 *
 * Sorted here rather than trusted from the API. `GET /chat/sessions` does order
 * by `updated_at desc`, but this list is merged with the in-flight conversation
 * before it is rendered, and a merge that assumed sortedness would put a
 * just-started chat in the middle of the menu.
 */
export function toConversations(sessions: ChatSession[]): Conversation[] {
  return sessions
    .map((session) => ({
      id: session.id,
      title: session.title,
      updatedAt: Date.parse(session.updated_at),
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}
