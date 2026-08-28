/**
 * Reading the finished answer back out of the transcript.
 *
 * `send()` is fire-and-forget: it returns nothing, and the answer arrives as
 * parts appended to the last assistant message. Matching by message id does not
 * work -- the provider mints a local id and then *replaces* it with the
 * server's on the `done` frame -- so the turn is detected from the status edge
 * and the answer is read from the tail of the list.
 */

import type { AgentMessage } from "../chat/types";

export type VoiceAnswer =
  | { kind: "text"; text: string }
  | { kind: "error" }
  | { kind: "empty" };

/**
 * The last assistant answer, or why there is not one.
 *
 * Text parts are joined rather than taking the first, because a turn that made
 * a tool call mid-answer produces several. An error part wins over any text:
 * half an answer followed by a failure should not be read out as an answer.
 */
export function answerFromMessages(messages: readonly AgentMessage[]): VoiceAnswer {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") return { kind: "empty" };
  if (last.parts.some((part) => part.type === "error")) return { kind: "error" };

  const text = last.parts
    .flatMap((part) => (part.type === "text" && part.text.trim() ? [part.text] : []))
    .join("\n\n")
    .trim();

  return text ? { kind: "text", text } : { kind: "empty" };
}
