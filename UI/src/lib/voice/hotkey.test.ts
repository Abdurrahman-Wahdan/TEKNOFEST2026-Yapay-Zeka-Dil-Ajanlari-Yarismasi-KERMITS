import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { isChatBusy, shouldOpenVoiceMode } from "./hotkey.ts";

const SPACE = {
  code: "Space",
  repeat: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
};

const FREE = {
  pathname: "/compare",
  inTextEntry: false,
  activatesOnSpace: false,
  blockingSurface: false,
  popupOpen: false,
  status: "ready" as const,
  signedIn: true,
  voiceBusy: false,
};

describe("opening voice mode with the space bar", () => {
  it("opens on a plain press when nothing has focus", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, FREE), true);
  });

  it("ignores the repeats a held key sends after the first", () => {
    assert.equal(shouldOpenVoiceMode({ ...SPACE, repeat: true }, FREE), false);
  });

  it("ignores every key that is not the space bar", () => {
    assert.equal(shouldOpenVoiceMode({ ...SPACE, code: "Enter" }, FREE), false);
    assert.equal(shouldOpenVoiceMode({ ...SPACE, code: "KeyK" }, FREE), false);
  });

  it("leaves a modified space to the browser", () => {
    for (const modifier of ["altKey", "ctrlKey", "metaKey", "shiftKey"] as const) {
      assert.equal(shouldOpenVoiceMode({ ...SPACE, [modifier]: true }, FREE), false);
    }
  });

  it("types a space into a field instead of listening", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, inTextEntry: true }), false);
  });

  it("lets a focused button keep the space bar that activates it", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, activatesOnSpace: true }), false);
  });

  it("stays out of the way while a dialog or menu owns the screen", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, blockingSurface: true }), false);
  });

  it("stays out of the way while the assistant panel is open", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, popupOpen: true }), false);
  });

  it("does nothing on the chat page, where the composer already has focus", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, pathname: "/chat" }), false);
  });

  it("refuses a second question while the first is still being answered", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, status: "submitted" }), false);
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, status: "streaming" }), false);
  });

  it("refuses a press while a voice turn is already running", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, voiceBusy: true }), false);
  });

  it("does nothing for a signed-out visitor", () => {
    assert.equal(shouldOpenVoiceMode(SPACE, { ...FREE, signedIn: false }), false);
  });
});

describe("reading the assistant's status", () => {
  it("counts a submitted and a streaming turn as busy", () => {
    assert.equal(isChatBusy("submitted"), true);
    assert.equal(isChatBusy("streaming"), true);
  });

  it("counts a finished turn as free", () => {
    assert.equal(isChatBusy("ready"), false);
  });
});
