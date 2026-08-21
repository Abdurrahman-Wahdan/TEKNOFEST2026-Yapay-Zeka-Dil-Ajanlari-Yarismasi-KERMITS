import type { AgentMessage } from "./types";

/**
 * Where past conversations are kept.
 *
 * The same seam `transport.ts` is: one interface, a local implementation now, and
 * a one-file swap when the backend lands. Nothing above this module knows that
 * history currently lives in the browser.
 *
 * localStorage rather than a cookie or sessionStorage: a transcript is far too
 * large for a cookie, and losing every past conversation when the tab closes
 * would make the history list pointless.
 */

export type StoredConversation = {
  id: string;
  /** FastAPI session id; absent for an old local-only conversation. */
  serverSessionId?: string;
  /** Derived from the first thing the user said -- see `titleFor`. */
  title: string;
  messages: AgentMessage[];
  /** Epoch ms, for ordering the list newest-first. */
  updatedAt: number;
};

export type ChatStore = {
  list: () => StoredConversation[];
  save: (conversation: StoredConversation) => void;
  remove: (id: string) => void;
  clear: () => void;
};

const KEY = "tf26.chat.history";

/** How many conversations to keep. Old ones fall off the end. */
const LIMIT = 50;

/**
 * A title from the first user message.
 *
 * The agent's own answer would read better, but it does not exist yet when a
 * conversation is first saved, and a list whose rows are titled "Untitled" until
 * a reply arrives is worse than one titled with the question.
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

function read(): StoredConversation[] {
  // Guarded for SSR and for browsers with storage disabled: this module is
  // imported by a client component that still renders on the server.
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Shape-checked rather than trusted. This is user-writable storage, and one
    // malformed entry from an older version of the app should not take the whole
    // history down with it.
    return parsed.filter(
      (c): c is StoredConversation =>
        Boolean(c) &&
        typeof (c as StoredConversation).id === "string" &&
        typeof (c as StoredConversation).title === "string" &&
        Array.isArray((c as StoredConversation).messages),
    );
  } catch {
    return [];
  }
}

function write(all: StoredConversation[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(all.slice(0, LIMIT)));
  } catch {
    /* storage full or blocked -- history is a convenience, not the app */
  }
}

/**
 * Subscription layer, so React can read this store without an effect.
 *
 * `useSyncExternalStore` is how the app already reads external state -- see
 * `src/lib/theme.ts` -- and it is what makes an SSR-safe read possible: the
 * server gets `EMPTY` and the client gets the real list, with no hydration
 * mismatch and no `setState` in a mount effect.
 *
 * The snapshot has to be *cached*. `getSnapshot` is called on every render, and
 * returning a freshly sorted array each time would be a new reference every
 * time, which React reads as "changed" and re-renders forever. It is invalidated
 * only when something actually writes.
 */
const listeners = new Set<() => void>();
let snapshot: StoredConversation[] | null = null;

/** A stable empty array: a new `[]` per call would loop for the same reason. */
const EMPTY: StoredConversation[] = [];

function emit(): void {
  snapshot = null;
  for (const listener of listeners) listener();
}

export function subscribeHistory(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function historySnapshot(): StoredConversation[] {
  if (!snapshot) snapshot = read().sort((a, b) => b.updatedAt - a.updatedAt);
  return snapshot;
}

/** What the server render sees: there is no storage there. */
export function historyServerSnapshot(): StoredConversation[] {
  return EMPTY;
}

/** The local implementation. Swap this for the backend when there is one. */
export const localChatStore: ChatStore = {
  list: () => read().sort((a, b) => b.updatedAt - a.updatedAt),

  save: (conversation) => {
    const all = read().filter((c) => c.id !== conversation.id);
    write([conversation, ...all].sort((a, b) => b.updatedAt - a.updatedAt));
    emit();
  },

  remove: (id) => {
    write(read().filter((c) => c.id !== id));
    emit();
  },

  clear: () => {
    write([]);
    emit();
  },
};

/** What the app talks to. */
export const chatStore: ChatStore = localChatStore;
