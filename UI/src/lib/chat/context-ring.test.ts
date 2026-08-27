import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { IMMINENT, NEAR, ringColor, ringTone } from "./context-ring.ts";

describe("ringTone", () => {
  const compactAt = 1000;

  it("is filling on an empty thread", () => {
    assert.equal(ringTone(0, compactAt), "filling");
  });

  it("stays filling for most of the way to compaction", () => {
    assert.equal(ringTone(compactAt * 0.5, compactAt), "filling");
  });

  it("turns near exactly at the threshold, not after it", () => {
    assert.equal(ringTone(compactAt * NEAR, compactAt), "near");
    assert.equal(ringTone(compactAt * NEAR - 1, compactAt), "filling");
  });

  it("turns imminent exactly at its threshold", () => {
    assert.equal(ringTone(compactAt * IMMINENT, compactAt), "imminent");
    assert.equal(ringTone(compactAt * IMMINENT - 1, compactAt), "near");
  });

  it("stays imminent once compaction is due or overdue", () => {
    assert.equal(ringTone(compactAt, compactAt), "imminent");
    assert.equal(ringTone(compactAt * 3, compactAt), "imminent");
  });

  it("does not go red when there is no threshold to be near", () => {
    // A misconfigured deployment must not paint every ring red.
    assert.equal(ringTone(5000, 0), "filling");
    assert.equal(ringTone(5000, -1), "filling");
  });
});

describe("ringColor", () => {
  it("maps each tone to its palette token", () => {
    assert.equal(ringColor(0, 1000), "var(--primary)");
    assert.equal(ringColor(800, 1000), "var(--warn)");
    assert.equal(ringColor(950, 1000), "var(--danger)");
  });

  it("returns tokens, never literal colours", () => {
    for (const used of [0, 800, 950, 5000]) {
      assert.match(ringColor(used, 1000), /^var\(--[a-z-]+\)$/);
    }
  });
});
