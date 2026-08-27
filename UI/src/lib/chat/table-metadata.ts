import type { AgentMessage } from "./types.ts";

/** The visible transcript, including the text bodies of attached context. */
export function conversationForTableMetadata(messages: AgentMessage[]) {
  return messages.flatMap((message) => {
    const content = message.parts
      .flatMap((part) => {
        if (part.type === "text") return [part.text];
        if (part.type === "context") {
          const heading = part.source ? `${part.label} (${part.source})` : part.label;
          return [part.body ? `${heading}\n${part.body}` : heading];
        }
        return [];
      })
      .filter((part) => part.trim() !== "")
      .join("\n\n");
    return content ? [{ role: message.role, content }] : [];
  });
}
