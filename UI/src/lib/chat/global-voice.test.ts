import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { isGlobalVoiceAvailable, isGlobalVoiceKey } from "./global-voice.ts";

describe("global voice eligibility", () => {
  it("accepts an unmodified first Space keydown", () => {
    assert.equal(
      isGlobalVoiceKey({
        code: "Space",
        repeat: false,
        altKey: false,
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
      }),
      true,
    );
  });

  it("rejects keyboard repeat and modified shortcuts", () => {
    const base = {
      code: "Space",
      repeat: false,
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
    };
    assert.equal(isGlobalVoiceKey({ ...base, repeat: true }), false);
    assert.equal(isGlobalVoiceKey({ ...base, ctrlKey: true }), false);
    assert.equal(isGlobalVoiceKey({ ...base, code: "Enter" }), false);
  });

  it("is unavailable on chat, in the popup, while busy, or signed out", () => {
    const base = {
      pathname: "/profile",
      popupOpen: false,
      status: "ready" as const,
      signedIn: true,
    };
    assert.equal(isGlobalVoiceAvailable(base), true);
    assert.equal(isGlobalVoiceAvailable({ ...base, pathname: "/chat" }), false);
    assert.equal(isGlobalVoiceAvailable({ ...base, popupOpen: true }), false);
    assert.equal(isGlobalVoiceAvailable({ ...base, status: "streaming" }), false);
    assert.equal(isGlobalVoiceAvailable({ ...base, signedIn: false }), false);
  });
});
