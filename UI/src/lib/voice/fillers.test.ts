import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  FILLER_OPENING_MS,
  FILLER_REPEAT_MS,
  fillerDelayMs,
  isOpeningFiller,
} from "./fillers.ts";

describe("deciding when to say it", () => {
  it("acknowledges the question with no wait at all", () => {
    // The whole point of the rewrite: the user hears something the moment they
    // stop talking, not ten seconds later.
    assert.equal(fillerDelayMs(0), 0);
    assert.equal(FILLER_OPENING_MS, 0);
  });

  it("repeats on a ten-second beat after that", () => {
    assert.equal(fillerDelayMs(1), FILLER_REPEAT_MS);
    assert.equal(FILLER_REPEAT_MS, 10_000);
  });

  it("keeps the same gap however long the wait runs", () => {
    // No jitter and no back-off. A drifting cadence is what let the old
    // twenty-second gaps read as the assistant having lost interest.
    for (const attempt of [1, 2, 3, 12, 400]) {
      assert.equal(fillerDelayMs(attempt), FILLER_REPEAT_MS);
    }
  });

  it("treats a nonsense attempt count as the opening rather than a long wait", () => {
    assert.equal(fillerDelayMs(-1), FILLER_OPENING_MS);
  });
});

describe("choosing what to say", () => {
  it("opens with the acknowledgement and holds with the other line", () => {
    assert.equal(isOpeningFiller(0), true);
    assert.equal(isOpeningFiller(1), false);
  });

  it("never returns to the opening line once the wait is under way", () => {
    for (const attempt of [1, 2, 3, 9, 100]) {
      assert.equal(isOpeningFiller(attempt), false, `attempt ${attempt}`);
    }
  });
});
