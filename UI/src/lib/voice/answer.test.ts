import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { answerFromMessages } from "./answer.ts";
import type { AgentMessage } from "../chat/types";

const assistant = (parts: AgentMessage["parts"]): AgentMessage => ({
  id: "a1",
  role: "assistant",
  parts,
});

describe("finding the finished answer", () => {
  it("reads the answer off the last assistant message", () => {
    const result = answerFromMessages([
      { id: "u1", role: "user", parts: [{ type: "text", text: "kâr payı?" }] },
      assistant([{ type: "text", text: "Kuveyt Türk yüzde 2,69." }]),
    ]);
    assert.deepEqual(result, { kind: "text", text: "Kuveyt Türk yüzde 2,69." });
  });

  it("joins an answer that arrived in several pieces", () => {
    const result = answerFromMessages([
      assistant([
        { type: "text", text: "Kuveyt Türk yüzde 2,69." },
        { type: "text", text: "Vakıf yüzde 2,55." },
      ]),
    ]);
    assert.deepEqual(result, {
      kind: "text",
      text: "Kuveyt Türk yüzde 2,69.\n\nVakıf yüzde 2,55.",
    });
  });

  it("reports a failure rather than reading a half answer aloud", () => {
    const result = answerFromMessages([
      assistant([
        { type: "text", text: "Kuveyt Türk yüzde" },
        { type: "error", message: "bağlantı koptu" },
      ]),
    ]);
    assert.deepEqual(result, { kind: "error" });
  });

  it("reports nothing when the assistant produced no words", () => {
    assert.deepEqual(answerFromMessages([assistant([{ type: "text", text: "" }])]), {
      kind: "empty",
    });
  });

  it("ignores the parts that are not the answer", () => {
    const result = answerFromMessages([
      assistant([
        { type: "citations", sources: [] },
        { type: "context", kind: "page", label: "/compare" },
        { type: "text", text: "Yüzde 2,69." },
      ]),
    ]);
    assert.deepEqual(result, { kind: "text", text: "Yüzde 2,69." });
  });

  it("reports nothing while the user's turn is still the last one", () => {
    const result = answerFromMessages([
      { id: "u1", role: "user", parts: [{ type: "text", text: "merhaba" }] },
    ]);
    assert.deepEqual(result, { kind: "empty" });
  });

  it("reports nothing for an empty transcript", () => {
    assert.deepEqual(answerFromMessages([]), { kind: "empty" });
  });
});
