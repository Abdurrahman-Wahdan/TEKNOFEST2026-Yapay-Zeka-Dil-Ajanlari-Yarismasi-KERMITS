import type { CapturePayload } from "./types";

/**
 * A picture of the page, for the questions text cannot answer.
 *
 * Secondary to `page-outline` on purpose: the outline is exact, cheap and
 * testable, and a table read from pixels invites misreading a rate. This is for
 * "does this look broken", "what is overlapping what" -- questions about the
 * rendering rather than the data.
 *
 * **snapdom, not html2canvas.** html2canvas reimplements the browser's renderer in
 * JavaScript and throws on `oklch()` and `color-mix()`; every derived token in
 * `src/styles/tailwind.css` is a `color-mix()`, so it would fail on this app's own
 * chrome rather than merely being slow. snapdom serialises the subtree and lets the
 * *browser* rasterise, so modern colour functions are never parsed by library code.
 *
 * `captureOptions` is pure and tested; `capturePage` is the thin call around it.
 */

/**
 * The widest image worth sending.
 *
 * A full-page PNG at device resolution is 1-3MB, and base64 on a JSON request
 * makes that a >4MB POST. At 1280 wide as WebP this lands in the low hundreds of
 * KB and stays legible -- and legible is the whole requirement, since nothing is
 * measured off this image.
 */
export const MAX_CAPTURE_WIDTH = 1280;

/** Enough that text stays sharp; more is bytes nobody reads. */
export const CAPTURE_QUALITY = 0.85;

/**
 * What must not appear in the capture.
 *
 * All of it is `position: fixed` chrome floating over the page: the drawer, the
 * assistant panel and its launcher, the selection button, and Next's dev
 * indicator. The assistant asking to see the page does not need a picture of
 * itself, and the drawer would take a quarter of every capture.
 */
export const CAPTURE_EXCLUDE = [
  "[data-no-capture]",
  ".MuiDrawer-root",
  "[role='dialog']",
  "[data-nextjs-toast]",
  "nextjs-portal",
];

export type CaptureOptions = {
  format: "webp";
  quality: number;
  scale: number;
  /**
   * Pinned to 1, and this is load-bearing.
   *
   * snapdom multiplies `scale` by the device pixel ratio, so on a retina screen
   * every capture came out at twice the intended size -- a 1280 cap produced a
   * 2134px image. Fixing the ratio makes `scale` the only thing deciding the
   * output, which is the only way the cap can mean anything.
   */
  dpr: number;
  backgroundColor?: string;
  embedFonts: boolean;
  exclude: string[];
};

/**
 * Options for one capture.
 *
 * `scale` is derived rather than fixed at 1: a 400px-wide layout captured at scale
 * 1 is a 400px image, which is unreadable, while a 2560px one needs scaling
 * *down*. Capping at 2 stops a small layout being blown up into a pointlessly
 * enormous file.
 *
 * Takes the width of the **element being captured**, not the viewport. They are
 * not the same -- the page content sits inside the drawer's margin and its own
 * padding -- and scaling by the wrong one puts the output on the wrong side of
 * the cap.
 */
export function captureOptions(
  elementWidth: number,
  backgroundColor?: string,
): CaptureOptions {
  const width = Math.max(1, Math.round(elementWidth));
  // A ceiling but no floor. A floor of 0.5 was here and it silently broke the
  // cap: a 4000px-wide layout needs 0.32 to reach 1280, and clamping to 0.5 gave
  // a 2000px image -- larger than the cap exists to prevent. Very wide layouts do
  // end up small, and that is the right trade: unreadably small is what
  // `read_page` is for.
  const scale = Math.min(2, MAX_CAPTURE_WIDTH / width);
  return {
    format: "webp",
    quality: CAPTURE_QUALITY,
    scale,
    dpr: 1,
    // An explicit background, because the page paints its own on `body` and a
    // captured subtree would otherwise come out transparent -- which composites
    // to black in most viewers and makes light-mode text invisible.
    backgroundColor,
    // Icon glyphs come from a cross-origin Google font; without this they drop
    // out of the image and every button becomes a blank square.
    embedFonts: true,
    exclude: CAPTURE_EXCLUDE,
  };
}

/**
 * An image in the shape a vision model actually takes.
 *
 * Every current vision API -- Anthropic's `{type: "image", source: {type:
 * "base64", media_type, data}}`, OpenAI's equivalent -- wants the media type and
 * the base64 payload as separate fields. A `data:` URL is neither: it is one
 * string with the media type welded to the front, and a backend handed that has to
 * strip the prefix before it can build a content block. Get it wrong and the model
 * is passed the literal characters `data:image/webp;base64,UklGR...` as *text* --
 * it sees nothing, and the tokens are billed anyway.
 *
 * So the wire carries the split form and the client keeps the data URL for its own
 * `<img src>`. Splitting here means the seam is unambiguous about what this is.
 */
export type ImagePayload = {
  /** e.g. `image/webp` -- straight into `source.media_type`. */
  mediaType: string;
  /** base64, with no `data:` prefix -- straight into `source.data`. */
  data: string;
};

/**
 * Pull a data URL apart into the two fields an image block needs.
 *
 * Returns null for anything that is not base64-encoded: a `blob:` URL or a plain
 * `data:` URL carries no bytes we can forward, and sending it as though it did
 * would put an unreadable string in front of the model.
 */
export function splitDataUrl(dataUrl: string): ImagePayload | null {
  // `[\s\S]` rather than the `s` flag, which needs an es2018 target.
  const match = /^data:([^;,]+);base64,([\s\S]+)$/.exec(dataUrl);
  if (!match) return null;
  const [, mediaType, data] = match;
  if (!mediaType.startsWith("image/") || !data) return null;
  return { mediaType, data };
}

/** How big the capture came out, for the label the agent sees. */
export type Capture = { dataUrl: string; width: number; height: number; bytes: number };

/** Roughly how many bytes a data URL carries, without decoding it. */
export function dataUrlBytes(dataUrl: string): number {
  const comma = dataUrl.indexOf(",");
  if (comma === -1) return 0;
  const body = dataUrl.length - comma - 1;
  // base64 is 4 characters per 3 bytes, minus any padding.
  const padding = dataUrl.endsWith("==") ? 2 : dataUrl.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((body * 3) / 4) - padding);
}

/**
 * Photograph the page's content area.
 *
 * Imported lazily: snapdom is only needed the first time anything asks for a
 * capture, and most sessions never will.
 */
export async function capturePage(): Promise<Capture | null> {
  if (typeof window === "undefined") return null;
  const root = document.querySelector("[data-page-root]");
  if (!root) return null;

  const background =
    getComputedStyle(document.body).backgroundColor ||
    getComputedStyle(document.documentElement).backgroundColor ||
    undefined;

  const { snapdom } = await import("@zumer/snapdom");
  // The element's own width, including anything it scrolls horizontally: that is
  // what snapdom will draw, and so what the cap has to be measured against.
  const element = root as HTMLElement;
  const options = captureOptions(
    Math.max(element.scrollWidth, element.getBoundingClientRect().width),
    background,
  );
  const image = await snapdom.toWebp(element, options);

  return {
    dataUrl: image.src,
    width: image.naturalWidth || image.width,
    height: image.naturalHeight || image.height,
    bytes: dataUrlBytes(image.src),
  };
}

/**
 * Staged captures, as the request's image payloads.
 *
 * Anything that cannot be split is dropped rather than sent half-formed: a capture
 * the model cannot decode is worse than a capture it was never offered, because
 * the agent would answer as though it had seen the page.
 */
export function toCapturePayloads(
  staged: { id: string; label: string; dataUrl: string; width: number; height: number }[],
): CapturePayload[] {
  const out: CapturePayload[] = [];
  for (const capture of staged) {
    const image = splitDataUrl(capture.dataUrl);
    if (!image) continue;
    out.push({
      id: capture.id,
      label: capture.label,
      mediaType: image.mediaType,
      data: image.data,
      width: capture.width,
      height: capture.height,
    });
  }
  return out;
}
