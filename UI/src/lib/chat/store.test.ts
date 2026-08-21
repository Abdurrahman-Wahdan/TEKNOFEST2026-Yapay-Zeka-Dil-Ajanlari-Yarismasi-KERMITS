import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ensureUniqueMessageIds, type StoredConversation } from "./store.ts";

const conversation = (ids: string[]): StoredConversation => ({
  id: "chat-1",
  title: "Chat",
  updatedAt: 1,
  messages: ids.map((id, index) => ({
    id,
    role: index % 2 === 0 ? "user" : "assistant",
    parts: [{ type: "text", text: String(index) }],
  })),
});

describe("ensureUniqueMessageIds", () => {
  it("repairs duplicate React keys deterministically", () => {
    const repaired = ensureUniqueMessageIds(
      conversation(["user-3", "assistant-4", "user-3", "assistant-4"]),
    );

    assert.deepEqual(
      repaired.messages.map((message) => message.id),
      ["user-3", "assistant-4", "user-3-chat-1-2", "assistant-4-chat-1-3"],
    );
    assert.deepEqual(
      ensureUniqueMessageIds(repaired).messages.map((message) => message.id),
      repaired.messages.map((message) => message.id),
    );
  });

  it("preserves the conversation object when every key is already unique", () => {
    const original = conversation(["user-a", "assistant-b"]);
    assert.equal(ensureUniqueMessageIds(original), original);
  });
});
