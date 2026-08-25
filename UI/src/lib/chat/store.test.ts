import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { ChatMessage, ChatSession, ChatSessionDetail } from "@/lib/api";

import {
  titleFor,
  toAgentMessage,
  toAgentMessages,
  toConversations,
} from "./store.ts";
import type { AgentMessage } from "./types.ts";

const message = (over: Partial<ChatMessage> = {}): ChatMessage => ({
  id: "m-1",
  role: "user",
  content: "Altın fiyatları nedir?",
  citations: [],
  parts: [],
  created_at: "2026-08-25T12:00:00+00:00",
  ...over,
});

const session = (over: Partial<ChatSession> = {}): ChatSession => ({
  id: "s-1",
  title: "Altın fiyatları",
  created_at: "2026-08-25T09:00:00+00:00",
  updated_at: "2026-08-25T12:00:00+00:00",
  ...over,
});

describe("toAgentMessage", () => {
  it("keeps the parts the server stored, and their order", () => {
    const stored = toAgentMessage(
      message({
        role: "assistant",
        content: "Gram altın 7.150 TL",
        parts: [
          { type: "context", kind: "capture", label: "Sayfaya bakıldı" },
          { type: "text", text: "Gram altın 7.150 TL" },
          { type: "citations", sources: [{ url: "https://x" }] },
        ],
      }),
    );

    assert.deepEqual(
      stored.parts.map((part) => part.type),
      ["context", "text", "citations"],
    );
  });

  it("rebuilds a text part for a turn stored before parts existed", () => {
    // Every conversation already in the table is one of these.
    const restored = toAgentMessage(message({ parts: [] }));
    assert.deepEqual(restored.parts, [
      { type: "text", text: "Altın fiyatları nedir?" },
    ]);
  });

  it("keeps the database id, so a re-fetch does not remount the transcript", () => {
    assert.equal(toAgentMessage(message({ id: "uuid-7" })).id, "uuid-7");
  });

  it("drops a part whose type this client does not know", () => {
    // A client that has not been deployed yet, reading a part written by a newer
    // server. The rest of the turn must still render.
    const restored = toAgentMessage(
      message({
        parts: [
          { type: "reasoning", text: "..." },
          { type: "text", text: "Merhaba" },
        ],
      }),
    );
    assert.deepEqual(restored.parts, [{ type: "text", text: "Merhaba" }]);
  });

  it("falls back to content when every part was unrecognisable", () => {
    // Rather than an empty bubble on a turn that has text in it.
    const restored = toAgentMessage(
      message({ content: "Merhaba", parts: [{ type: "reasoning" }, 7, null] as never }),
    );
    assert.deepEqual(restored.parts, [{ type: "text", text: "Merhaba" }]);
  });

  it("survives a missing parts field entirely", () => {
    // `parts` is optional in the generated type, so a client reading a response
    // from a server that predates the column must not crash on it.
    const withoutParts = { ...message() };
    delete (withoutParts as { parts?: unknown }).parts;
    const restored = toAgentMessage(withoutParts as ChatMessage);
    assert.deepEqual(restored.parts, [
      { type: "text", text: "Altın fiyatları nedir?" },
    ]);
  });
});

describe("toAgentMessages", () => {
  it("maps a whole conversation in order", () => {
    const detail: ChatSessionDetail = {
      ...session(),
      messages: [
        message({ id: "a", content: "soru" }),
        message({ id: "b", role: "assistant", content: "cevap" }),
      ],
    };
    assert.deepEqual(
      toAgentMessages(detail).map((m) => [m.id, m.role]),
      [
        ["a", "user"],
        ["b", "assistant"],
      ],
    );
  });

  it("returns nothing for a conversation with no turns", () => {
    assert.deepEqual(toAgentMessages({ ...session(), messages: [] }), []);
  });
});

describe("toConversations", () => {
  it("orders newest first regardless of what the API returned", () => {
    // Sorted here because this list is merged with the in-flight conversation
    // before it is rendered, and a merge that assumed order would put a
    // just-started chat in the middle of the menu.
    const rows = toConversations([
      session({ id: "old", updated_at: "2026-08-20T10:00:00+00:00" }),
      session({ id: "new", updated_at: "2026-08-25T10:00:00+00:00" }),
      session({ id: "mid", updated_at: "2026-08-22T10:00:00+00:00" }),
    ]);
    assert.deepEqual(
      rows.map((row) => row.id),
      ["new", "mid", "old"],
    );
  });

  it("carries the server's own title", () => {
    const [row] = toConversations([session({ title: "Konut finansmanı" })]);
    assert.equal(row.title, "Konut finansmanı");
  });
});

describe("titleFor", () => {
  const user = (parts: AgentMessage["parts"]): AgentMessage[] => [
    { id: "u", role: "user", parts },
  ];

  it("names the in-flight conversation from the first thing typed", () => {
    assert.equal(
      titleFor(user([{ type: "text", text: "  Altın fiyatları nedir?  " }]), "…"),
      "Altın fiyatları nedir?",
    );
  });

  it("falls back to an attachment's label when nothing was typed", () => {
    assert.equal(
      titleFor(
        user([{ type: "context", kind: "table", label: "Konut Finansmanı" }]),
        "…",
      ),
      "Konut Finansmanı",
    );
  });

  it("unwraps a mention, because the brackets are machinery", () => {
    assert.equal(
      titleFor(user([{ type: "text", text: "@[rapor.pdf] özetle" }]), "…"),
      "rapor.pdf özetle",
    );
  });

  it("cuts a long question at 48 characters", () => {
    const long = "a".repeat(200);
    const title = titleFor(user([{ type: "text", text: long }]), "…");
    assert.equal(title.length, 49); // 48 plus the ellipsis
    assert.ok(title.endsWith("…"));
  });

  it("uses the fallback for a turn with nothing nameable in it", () => {
    assert.equal(titleFor([], "…"), "…");
  });
});
