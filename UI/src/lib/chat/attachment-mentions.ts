import type { AgentMessage, MentionTarget } from "./types";

/** A previously prepared file that can be attached to another turn. */
export type ReusableAttachment = {
  id: string;
  filename: string;
  kind: "image" | "text" | "document";
  pageCount?: number;
};

const keyFor = (filename: string) => filename.trim().toLocaleLowerCase();

/**
 * Read the reusable attachment handles out of the visible conversation.
 *
 * Newest wins when the same filename was uploaded more than once. A mention is
 * deliberately human-readable (`@[statement.pdf]`), so a duplicate name cannot
 * encode which upload was intended; using the latest matches ordinary chat UX.
 */
export function conversationAttachments(messages: AgentMessage[]): ReusableAttachment[] {
  const seen = new Set<string>();
  const found: ReusableAttachment[] = [];

  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = messages[messageIndex];
    for (let partIndex = message.parts.length - 1; partIndex >= 0; partIndex -= 1) {
      const part = message.parts[partIndex];
      if (part.type !== "attachment" || !part.attachmentId) continue;
      const key = keyFor(part.filename);
      if (seen.has(key)) continue;
      seen.add(key);
      found.push({
        id: part.attachmentId,
        filename: part.filename,
        kind: part.kind,
        pageCount: part.pageCount,
      });
    }
  }

  return found.reverse();
}

/** Prefer newly staged files, then add distinct files from earlier turns. */
export function mergeReusableAttachments(
  staged: ReusableAttachment[],
  previous: ReusableAttachment[],
): ReusableAttachment[] {
  const seen = new Set<string>();
  return [...staged, ...previous].filter((attachment) => {
    const key = keyFor(attachment.filename);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Convert prepared files into the composer's flattened mention rows. */
export function attachmentMentionTargets(
  attachments: ReusableAttachment[],
): MentionTarget[] {
  return attachments.map((attachment) => ({
    id: `prepared-${attachment.id}`,
    filename: attachment.filename,
    kind: attachment.kind === "image" ? "image" : "file",
  }));
}

/** Resolve only closed file tokens, never every file the conversation has seen. */
export function mentionedAttachments(
  text: string,
  available: ReusableAttachment[],
): ReusableAttachment[] {
  const byName = new Map(available.map((attachment) => [keyFor(attachment.filename), attachment]));
  const selected: ReusableAttachment[] = [];
  const seenIds = new Set<string>();

  for (const match of text.matchAll(/@\[([^\]\r\n]+)\]/g)) {
    const attachment = byName.get(keyFor(match[1]));
    if (!attachment || seenIds.has(attachment.id)) continue;
    seenIds.add(attachment.id);
    selected.push(attachment);
  }

  return selected;
}
