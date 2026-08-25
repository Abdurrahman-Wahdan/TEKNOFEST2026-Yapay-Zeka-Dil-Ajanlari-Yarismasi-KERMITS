import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { mentionAt } from "./mention.ts";

describe("mentionAt", () => {
  it("opens at the start of a message", () => {
    assert.deepEqual(mentionAt("@notes", 6), { start: 0, query: "notes" });
  });

  it("keeps filenames with spaces searchable", () => {
    assert.deepEqual(mentionAt("compare @bank statement.pdf", 27), {
      start: 8,
      query: "bank statement.pdf",
    });
  });

  it("does not reopen a selected filename token", () => {
    assert.equal(mentionAt("compare @[bank statement.pdf] ", 30), null);
  });

  it("does not treat the middle of an email address as a mention", () => {
    assert.equal(mentionAt("mail user@example.com", 21), null);
  });

  it("uses only the current line and the latest mention", () => {
    assert.deepEqual(mentionAt("@old\ncheck @new file", 20), {
      start: 11,
      query: "new file",
    });
  });

  it("closes when the caller moves the synthetic caret away", () => {
    assert.equal(mentionAt("@notes", -1), null);
  });
});
