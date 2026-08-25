"use client";

import { capturePage, splitDataUrl } from "./capture";
import { outlinePage, outlineToMarkdown } from "./page-outline";
import type { ClientToolName, PageViewMode, ToolResult } from "./types";

/**
 * The one thing the agent can ask the browser to do on its behalf: look at the
 * page the user is on.
 *
 * A registry with a closed name, not a name the backend picks freely -- this runs
 * client code because something on the wire asked it to, so an unknown name is
 * refused rather than dispatched.
 *
 * One tool with a `mode`, not two tools. Looking at the page is a single
 * capability with two representations of the same thing, and `read_page` /
 * `capture_page` as separate calls forced the agent to commit before it knew which
 * it needed -- then cost a second round trip whenever it guessed wrong.
 *
 * Which mode to ask for:
 *
 * - `text` for anything about the *data*. The outline carries exact figures and
 *   the current filter state; a rate read off an image is a guess, and `2,89%`
 *   misread as `289` is not a cosmetic error in a finance app.
 * - `image` for anything about the *rendering* -- what overlaps what, whether
 *   something looks broken.
 * - `both` when unsure, which is most of the time. It is one round trip.
 */

/** How a tool reports back, including when it could not run. */
type Runner = (id: string, mode: PageViewMode) => Promise<ToolResult>;

/** The page's content area. Everything outside it is fixed chrome. */
function pageRoot(): Element | null {
  return document.querySelector("[data-page-root]");
}

/**
 * The path without its locale prefix, matching what `usePathname` reports
 * everywhere else. This runs outside React, so the next-intl helper is not here.
 */
function stripLocale(pathname: string): string {
  return pathname.replace(/^\/(tr|en)(?=\/|$)/, "") || "/";
}

/** The page as text. Returns undefined when there is no page to read. */
export function readPageText(): string | undefined {
  const root = pageRoot();
  if (!root) return undefined;
  return outlineToMarkdown(outlinePage(root, stripLocale(window.location.pathname)));
}

const TOOLS: Record<ClientToolName, Runner> = {
  async look_at_page(id, mode) {
    const wantsText = mode === "text" || mode === "both";
    const wantsImage = mode === "image" || mode === "both";

    const text = wantsText ? readPageText() : undefined;

    let image: ToolResult["image"];
    if (wantsImage) {
      const capture = await capturePage();
      const split = capture ? splitDataUrl(capture.dataUrl) : null;
      // A capture the model cannot decode is worse than one it was never offered:
      // it would answer as though it had seen the page. Dropped, not forwarded.
      if (capture && split) {
        image = {
          id,
          label: `${capture.width}×${capture.height}`,
          mediaType: split.mediaType,
          data: split.data,
          width: capture.width,
          height: capture.height,
        };
      }
    }

    if (!text && !image) {
      return {
        id,
        name: "look_at_page",
        label: "page unavailable",
        text: "(there is no page on screen to look at)",
      };
    }

    return { id, name: "look_at_page", text, image, label: "looked at the page" };
  },
};

/**
 * Run one tool call.
 *
 * Never throws: a tool that failed still has to answer, or the agent waits on a
 * turn that never comes back. The failure travels as the result.
 */
export async function runClientTool(
  name: string,
  id: string,
  mode: PageViewMode = "both",
): Promise<ToolResult> {
  const runner = TOOLS[name as ClientToolName];
  if (!runner) {
    return {
      id,
      name: "look_at_page",
      label: `unknown tool "${name}"`,
      text: `(this client has no tool called "${name}")`,
    };
  }
  try {
    return await runner(id, mode);
  } catch (error) {
    return {
      id,
      name: "look_at_page",
      label: "could not look at the page",
      text: `(looking at the page failed: ${
        error instanceof Error ? error.message : String(error)
      })`,
    };
  }
}
