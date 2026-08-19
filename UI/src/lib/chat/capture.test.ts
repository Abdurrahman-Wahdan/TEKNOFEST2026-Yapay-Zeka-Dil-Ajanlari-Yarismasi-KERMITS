import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  CAPTURE_EXCLUDE,
  MAX_CAPTURE_WIDTH,
  captureOptions,
  dataUrlBytes,
  splitDataUrl,
  toCapturePayloads,
} from "./capture.ts";

describe("captureOptions", () => {
  it("scales a narrow viewport up so the text is legible", () => {
    // A 400px phone captured at scale 1 is a 400px image, which no model can
    // read. The point of a capture is that it can be looked at. It stops at the
    // x2 ceiling rather than reaching the width cap -- 800px of a phone layout is
    // already legible, and x3.2 would only add bytes.
    const { scale } = captureOptions(400);
    assert.equal(scale, 2);
    assert.ok(400 * scale <= MAX_CAPTURE_WIDTH);
  });

  it("scales a wide viewport down to the cap", () => {
    const { scale } = captureOptions(2560);
    assert.equal(Math.round(2560 * scale), MAX_CAPTURE_WIDTH);
  });

  it("never scales beyond 2, however narrow the viewport", () => {
    // A retina desktop scaled without a ceiling produces a pointlessly enormous
    // file for no extra readability.
    assert.equal(captureOptions(100).scale, 2);
    assert.equal(captureOptions(1).scale, 2);
  });

  it("keeps scaling down however wide, rather than breaking the cap", () => {
    // There is deliberately no lower bound: a floor of 0.5 meant a 4000px layout
    // produced a 2000px image, which is exactly what the cap exists to prevent.
    const { scale } = captureOptions(100000);
    assert.ok(scale < 0.5);
    assert.ok(100000 * scale <= MAX_CAPTURE_WIDTH + 1);
  });

  it("survives a nonsense viewport width rather than dividing by zero", () => {
    for (const width of [0, -50, 0.2]) {
      const { scale } = captureOptions(width);
      assert.ok(Number.isFinite(scale), `${width} -> ${scale}`);
      assert.ok(scale > 0);
    }
  });

  it("pins the device pixel ratio to 1", () => {
    // snapdom multiplies `scale` by the DPR, so on a retina screen the 1280 cap
    // was producing 2134px images. Without this the cap means nothing.
    assert.equal(captureOptions(1280).dpr, 1);
    assert.equal(captureOptions(400).dpr, 1);
  });

  it("keeps the output within the width cap", () => {
    for (const width of [320, 800, 1010, 1280, 2560, 4000]) {
      const { scale } = captureOptions(width);
      // The x2 ceiling can exceed the cap for very narrow layouts, which is
      // deliberate; everything from a phone upwards must land inside it.
      if (width >= MAX_CAPTURE_WIDTH / 2) {
        assert.ok(
          width * scale <= MAX_CAPTURE_WIDTH + 1,
          `${width} x ${scale} = ${width * scale}`,
        );
      }
    }
  });

  it("asks for webp and embeds fonts", () => {
    // Icon glyphs come from a cross-origin Google font; without embedding they
    // drop out and every button in the image becomes a blank square.
    const o = captureOptions(1280, "#fff");
    assert.equal(o.format, "webp");
    assert.equal(o.embedFonts, true);
    assert.equal(o.quality, 0.85);
  });

  it("carries an explicit background", () => {
    // A captured subtree paints no background of its own -- the page paints it on
    // `body` -- and transparent composites to black, hiding light-mode text.
    assert.equal(captureOptions(1280, "rgb(255, 255, 255)").backgroundColor, "rgb(255, 255, 255)");
  });

  it("excludes the fixed chrome floating over the page", () => {
    // The assistant asking to see the page does not need a picture of itself,
    // and the drawer would take a quarter of every capture.
    const o = captureOptions(1280);
    for (const selector of ["[role='dialog']", ".MuiDrawer-root", "[data-no-capture]"]) {
      assert.ok(o.exclude.includes(selector), selector);
    }
    assert.deepEqual(o.exclude, CAPTURE_EXCLUDE);
  });
});

describe("dataUrlBytes", () => {
  it("sizes a data URL without decoding it", () => {
    // "AAAA" is 4 base64 characters, so 3 bytes.
    assert.equal(dataUrlBytes("data:image/webp;base64,AAAA"), 3);
  });

  it("accounts for padding", () => {
    assert.equal(dataUrlBytes("data:image/webp;base64,AA=="), 1);
    assert.equal(dataUrlBytes("data:image/webp;base64,AAA="), 2);
  });

  it("returns zero for something that is not a data URL", () => {
    assert.equal(dataUrlBytes("blob:https://x/y"), 0);
    assert.equal(dataUrlBytes(""), 0);
  });
});

describe("splitDataUrl", () => {
  it("splits a data URL into the two fields an image block needs", () => {
    // Anthropic's `source` takes `media_type` and `data` separately. A data URL
    // forwarded whole is one string with the type welded to the front.
    assert.deepEqual(splitDataUrl("data:image/webp;base64,UklGRg=="), {
      mediaType: "image/webp",
      data: "UklGRg==",
    });
  });

  it("handles a payload containing newlines", () => {
    // Base64 can be wrapped, and a `.` without the `s` flag stops at a newline --
    // which would silently truncate the image.
    const out = splitDataUrl("data:image/png;base64,AAAA\nBBBB");
    assert.equal(out?.data, "AAAA\nBBBB");
  });

  it("refuses anything that is not a base64 image", () => {
    // Sending an undecodable value as though it were an image is worse than
    // sending nothing: the agent answers as if it had seen the page.
    assert.equal(splitDataUrl("blob:https://example/abc"), null);
    assert.equal(splitDataUrl("data:image/webp,notbase64"), null);
    assert.equal(splitDataUrl("data:text/plain;base64,aGk="), null);
    assert.equal(splitDataUrl("data:image/webp;base64,"), null);
    assert.equal(splitDataUrl(""), null);
  });
});

describe("toCapturePayloads", () => {
  const staged = (dataUrl: string, id = "att-1") => ({
    id,
    label: "800×600",
    dataUrl,
    width: 800,
    height: 600,
  });

  it("carries the split image plus its dimensions", () => {
    const [out] = toCapturePayloads([staged("data:image/webp;base64,UklGRg==")]);
    assert.deepEqual(out, {
      id: "att-1",
      label: "800×600",
      mediaType: "image/webp",
      data: "UklGRg==",
      width: 800,
      height: 600,
    });
  });

  it("drops an unsplittable capture rather than sending it broken", () => {
    const out = toCapturePayloads([
      staged("data:image/webp;base64,UklGRg==", "good"),
      staged("blob:https://example/abc", "bad"),
    ]);
    assert.equal(out.length, 1);
    assert.equal(out[0].id, "good");
  });

  it("returns nothing for nothing", () => {
    assert.deepEqual(toCapturePayloads([]), []);
  });
});
