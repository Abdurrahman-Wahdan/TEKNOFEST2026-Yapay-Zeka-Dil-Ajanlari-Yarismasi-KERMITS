import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { conversationForTableMetadata } from "./table-metadata.ts";

describe("conversationForTableMetadata", () => {
  it("keeps visible prose and attached table bodies with their roles", () => {
    const result = conversationForTableMetadata([
      {
        id: "u1",
        role: "user",
        parts: [
          { type: "context", kind: "table", label: "Old quote", body: "| Bank | Rate |" },
          { type: "text", text: "Compare this" },
        ],
      },
      { id: "a1", role: "assistant", parts: [{ type: "text", text: "Here is the result." }] },
    ]);

    assert.deepEqual(result, [
      { role: "user", content: "Old quote\n| Bank | Rate |\n\nCompare this" },
      { role: "assistant", content: "Here is the result." },
    ]);
  });

  it("drops error-only messages rather than teaching the metadata agent an error", () => {
    assert.deepEqual(
      conversationForTableMetadata([
        { id: "a1", role: "assistant", parts: [{ type: "error", message: "network error" }] },
      ]),
      [],
    );
  });
});
