import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { speakableText } from "./speech-text.ts";

describe("speakableText", () => {
  it("drops the marks that only mean something to the eye", () => {
    assert.equal(
      speakableText("**Kuveyt Türk** en uygun *seçenek*, `%2,89` kâr oranıyla."),
      "Kuveyt Türk en uygun seçenek, %2,89 kâr oranıyla.",
    );
  });

  it("reads a link as the words it was written as", () => {
    assert.equal(
      speakableText("Detaylar [kampanya sayfasında](https://example.com/x)."),
      "Detaylar kampanya sayfasında.",
    );
  });

  it("ends a heading so the voice pauses after it", () => {
    assert.equal(
      speakableText("## Konut finansmanı\nOranlar bu hafta değişmedi."),
      "Konut finansmanı. Oranlar bu hafta değişmedi.",
    );
  });

  it("reads a table row as column and value", () => {
    assert.equal(
      speakableText(
        [
          "| Banka | Taksit |",
          "| --- | ---: |",
          "| Kuveyt Türk | 12.400 TL |",
          "| Albaraka | 12.910 TL |",
        ].join("\n"),
      ),
      "Banka: Kuveyt Türk, Taksit: 12.400 TL. Banka: Albaraka, Taksit: 12.910 TL.",
    );
  });

  it("names an empty cell rather than running two columns together", () => {
    assert.equal(
      speakableText(["| Banka | Taksit |", "| --- | --- |", "| Vakıf | |"].join("\n")),
      "Banka: Vakıf, Taksit: —.",
    );
  });

  it("drops a fenced code block", () => {
    assert.equal(
      speakableText("Şöyle:\n\n```json\n{\"a\": 1}\n```\n\nSonuç bu."),
      "Şöyle: Sonuç bu.",
    );
  });

  it("drops an unterminated fence, as a streamed answer would carry", () => {
    assert.equal(speakableText("Şöyle:\n\n```json\n{\"a\": 1}"), "Şöyle:");
  });

  it("reads list items as sentences", () => {
    assert.equal(
      speakableText("- Kuveyt Türk\n- Albaraka\n1. Vakıf Katılım"),
      "Kuveyt Türk. Albaraka. Vakıf Katılım.",
    );
  });

  it("drops a horizontal rule", () => {
    assert.equal(speakableText("Bir\n\n---\n\nİki"), "Bir İki");
  });

  it("keeps a bar that is not a table", () => {
    assert.equal(speakableText("A | B ayrımı yok"), "A | B ayrımı yok");
  });
});
