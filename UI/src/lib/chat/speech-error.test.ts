import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { speechErrorKind } from "./speech-error.ts";

describe("explaining why a reading did not happen", () => {
  it("reads a 503 as the model already reading something else", () => {
    assert.equal(speechErrorKind(503), "busy");
  });

  it("reads a 422 as the passage being refused", () => {
    assert.equal(speechErrorKind(422), "unavailable");
  });

  it("reads anything else as a plain failure", () => {
    assert.equal(speechErrorKind(500), "failed");
    assert.equal(speechErrorKind(401), "failed");
  });

  it("reads a failure with no status at all as a plain failure", () => {
    assert.equal(speechErrorKind(undefined), "failed");
  });
});
