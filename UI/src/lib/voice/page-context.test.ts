import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { voicePageContext } from "./page-context.ts";

const OUTLINE = "# Karşılaştırma\n\n| Banka | Oran |\n| --- | --- |\n| Vakıf | %2,55 |";

describe("carrying the page into a voice question", () => {
  it("attaches the page the user was looking at", () => {
    const context = voicePageContext(OUTLINE, "/compare", "Karşılaştırma", 3);
    assert.deepEqual(context, {
      id: "voice-page-3",
      kind: "page",
      label: "Karşılaştırma",
      body: OUTLINE,
      format: "markdown",
      location: { path: "/compare" },
    });
  });

  it("sends the outline whole, figures and all", () => {
    const long = `${OUTLINE}\n${"| Ziraat | %2,10 |\n".repeat(400)}`;
    assert.equal(voicePageContext(long, "/compare", "x", 1)?.body, long.trim());
  });

  it("attaches nothing on a page with no readable content", () => {
    assert.equal(voicePageContext(undefined, "/profile", "Profil", 1), null);
    assert.equal(voicePageContext("   \n ", "/profile", "Profil", 1), null);
  });

  it("gives each turn its own attachment id", () => {
    const first = voicePageContext(OUTLINE, "/compare", "x", 1);
    const second = voicePageContext(OUTLINE, "/compare", "x", 2);
    assert.notEqual(first?.id, second?.id);
  });
});
