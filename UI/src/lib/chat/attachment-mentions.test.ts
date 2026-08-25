import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  conversationAttachments,
  mentionedAttachments,
  mergeReusableAttachments,
} from "./attachment-mentions.ts";
import type { AgentMessage } from "./types.ts";

describe("conversation attachment mentions", () => {
  const messages: AgentMessage[] = [
    {
      id: "user-1",
      role: "user",
      parts: [
        {
          type: "attachment",
          attachmentId: "prepared-old-notes",
          filename: "notes.md",
          kind: "text",
        },
        {
          type: "attachment",
          attachmentId: "prepared-pdf",
          filename: "two pages.pdf",
          kind: "document",
          pageCount: 2,
        },
      ],
    },
    {
      id: "user-2",
      role: "user",
      parts: [
        {
          type: "attachment",
          attachmentId: "prepared-new-notes",
          filename: "NOTES.md",
          kind: "text",
        },
      ],
    },
  ];

  it("keeps prepared handles after the staging tray was cleared", () => {
    assert.deepEqual(conversationAttachments(messages), [
      {
        id: "prepared-pdf",
        filename: "two pages.pdf",
        kind: "document",
        pageCount: 2,
      },
      {
        id: "prepared-new-notes",
        filename: "NOTES.md",
        kind: "text",
        pageCount: undefined,
      },
    ]);
  });

  it("resolves only explicitly closed mention tokens", () => {
    const available = conversationAttachments(messages);
    assert.deepEqual(
      mentionedAttachments("Compare @[notes.md] with @[two pages.pdf].", available).map(
        ({ id }) => id,
      ),
      ["prepared-new-notes", "prepared-pdf"],
    );
    assert.deepEqual(mentionedAttachments("Typing @notes is not selected yet", available), []);
  });

  it("prefers a newly staged upload with the same filename", () => {
    const merged = mergeReusableAttachments(
      [{ id: "prepared-staged", filename: "notes.md", kind: "text" }],
      conversationAttachments(messages),
    );
    assert.equal(merged.find(({ filename }) => filename.toLowerCase() === "notes.md")?.id, "prepared-staged");
  });
});
