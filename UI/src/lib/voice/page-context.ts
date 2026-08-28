/**
 * The page the user is looking at, as context on the voice turn.
 *
 * Voice mode has no composer, so there is no `@` menu and no way to attach
 * anything by hand -- and it is the mode where attaching matters most, because
 * "bunlardan hangisi daha iyi?" only means something next to the table it was
 * asked in front of.
 *
 * It travels as an ordinary `AttachedContext`, the same shape the composer
 * builds for a page capture's outline, so the agent sees it on the first pass.
 * The alternative is letting the agent call `look_at_page` itself, which is a
 * whole extra round trip in the one mode where the wait is already the problem.
 */

import type { AttachedContext } from "../chat/types";

/**
 * Wrap a page outline for the request, or return null when there is no page.
 *
 * Never truncated. The outline carries exact figures and the live filter state,
 * and a rate cut off halfway is worse than no rate at all.
 */
export function voicePageContext(
  outline: string | undefined,
  path: string,
  label: string,
  runId: number,
): AttachedContext | null {
  const body = outline?.trim();
  if (!body) return null;
  return {
    id: `voice-page-${runId}`,
    kind: "page",
    label,
    body,
    format: "markdown",
    location: { path },
  };
}
