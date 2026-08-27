import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { speakableText, speechChunks } from "./speech-text.ts";

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

describe("speechChunks", () => {
  it("splits on sentence boundaries, not mid-sentence", () => {
    const chunks = speechChunks("Bir cümle. İki cümle. Üç cümle.", 16);
    assert.deepEqual(chunks, ["Bir cümle.", "İki cümle.", "Üç cümle."]);
  });

  it("keeps a sentence whole even when it is over the budget", () => {
    const long = `${"a".repeat(400)}.`;
    assert.deepEqual(speechChunks(long, 180), [long]);
  });

  it("packs short sentences together rather than one utterance each", () => {
    assert.deepEqual(speechChunks("Bir. İki. Üç.", 180), ["Bir. İki. Üç."]);
  });

  it("does not cut a Turkish thousands separator in half", () => {
    // 3.031.200 read as "3.031." then "200" is the number spoken as two
    // sentences with a pause down the middle of it.
    const text = "Toplam 3.031.200 TL tutuyor. Taksit 25.260 TL.";
    assert.deepEqual(speechChunks(text, 20), [
      "Toplam 3.031.200 TL tutuyor.",
      "Taksit 25.260 TL.",
    ]);
  });

  it("does not treat a date as three sentences", () => {
    assert.deepEqual(speechChunks("Oranlar 27.08.2026 tarihlidir.", 10), [
      "Oranlar 27.08.2026 tarihlidir.",
    ]);
  });

  it("reassembles losslessly, which is what makes cutting it safe", () => {
    const text = speakableText(
      "## Başlık\nBir cümle. Toplam 3.031.200 TL. Son cümle burada.",
    );
    assert.equal(speechChunks(text, 12).join(" "), text);
  });

  it("has nothing to say about an empty answer", () => {
    assert.deepEqual(speechChunks(""), []);
  });
});
