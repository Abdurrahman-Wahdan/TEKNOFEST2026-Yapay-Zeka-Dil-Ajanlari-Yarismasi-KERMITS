import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { normaliseAgentMarkdown } from "./markdown-normalize.ts";

describe("normaliseAgentMarkdown", () => {
  it("renders model-authored menu paths with Unicode arrows", () => {
    assert.equal(
      normaliseAgentMarkdown(
        "Mobil Şube $\\rightarrow$ Hesap $\\rightarrow$ Yatırım Hesabı Aç",
      ),
      "Mobil Şube → Hesap → Yatırım Hesabı Aç",
    );
  });

  it("repairs the double-escaped arrow form stored by the chat API", () => {
    assert.equal(
      normaliseAgentMarkdown(
        "`Yatırım $\\\\rightarrow$ Kira Sertifikası` menüsü",
      ),
      "`Yatırım → Kira Sertifikası` menüsü",
    );
  });

  it("supports the common arrow variants", () => {
    assert.equal(
      normaliseAgentMarkdown(
        "$\\leftarrow$ $\\leftrightarrow$ $\\Rightarrow$ $\\uparrow$ $\\downarrow$",
      ),
      "← ↔ ⇒ ↑ ↓",
    );
  });

  it("renders model-authored comparison symbols inside table cells", () => {
    assert.equal(
      normaliseAgentMarkdown(
        "| **Türkiye Finans** | %37,44 (Bireysel $\\ge$10k) | %40,12 (Bireysel $\\geq$10k) |",
      ),
      "| **Türkiye Finans** | %37,44 (Bireysel ≥10k) | %40,12 (Bireysel ≥10k) |",
    );
  });

  it("supports the common relation variants and doubled escaping", () => {
    assert.equal(
      normaliseAgentMarkdown(
        "$\\le$ $\\leq$ $\\ne$ $\\neq$ $\\approx$ $\\gt$ $\\lt$ $\\\\ge$",
      ),
      "≤ ≤ ≠ ≠ ≈ > < ≥",
    );
  });

  it("does not reinterpret code or ordinary dollar text", () => {
    const source = [
      "Price: $100",
      "`$\\rightarrow$`",
      "```text",
      "$\\rightarrow$",
      "$\\ge$",
      "```",
      "Outside $\\to$ here",
    ].join("\n");

    assert.equal(
      normaliseAgentMarkdown(source),
      [
        "Price: $100",
        "`$\\rightarrow$`",
        "```text",
        "$\\rightarrow$",
        "$\\ge$",
        "```",
        "Outside → here",
      ].join("\n"),
    );
  });

  it("preserves standalone arrow code and fenced examples", () => {
    const source = [
      "`$\\\\rightarrow$`",
      "```text",
      "Menu $\\\\rightarrow$ Account",
      "Threshold $\\\\ge$ 10k",
      "```",
    ].join("\n");

    assert.equal(normaliseAgentMarkdown(source), source);
  });
});
