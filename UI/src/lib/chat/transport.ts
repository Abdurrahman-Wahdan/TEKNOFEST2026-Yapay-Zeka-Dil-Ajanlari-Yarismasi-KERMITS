import { askStream } from "@/lib/api";
import type { ChatChunk, ChatRequest } from "./types";

/**
 * The one seam between the chat UI and the live banking agent.
 */

/** The last user text is the new server turn; the server owns full history. */
function lastUserText(request: ChatRequest): string {
  for (let i = request.messages.length - 1; i >= 0; i -= 1) {
    const message = request.messages[i];
    if (message.role !== "user") continue;
    const text = message.parts.find((p) => p.type === "text");
    return text && text.type === "text" ? text.text : "";
  }
  return "";
}

/**
 * The real call, for when the agent lands. Left in place rather than described in
 * a comment so the swap is a one-line change to the export below and not a
 * from-scratch write against a half-remembered protocol.
 *
 * `/api/*` is already proxied to `API_ORIGIN ?? http://127.0.0.1:8000` by
 * `next.config.ts`, so this needs no base URL.
 *
 * Assumes newline-delimited JSON, one `ChatChunk` per line -- adjust to whatever
 * the backend actually emits. The important part is the shape of this function,
 * not its body.
 *
 * **Images: the one thing the backend must get right.** `request.captures` and any
 * `toolResults[].image` carry `{mediaType, data}` -- base64 with no `data:` prefix.
 * They are split here so the server never has to parse a `data:` URL, and so the
 * bytes drop straight into whatever the runtime wants.
 *
 * The target is **Gemma 4** (Apache-2.0; E2B/E4B at 128k context, 12B/26B-A4B/31B at
 * 256k), which takes image *and* text input and lists screen/UI understanding and
 * chart comprehension among its vision capabilities -- so a page capture is
 * something it is actually built to read. Its chat template takes
 *
 *     {"type": "image", "image": <PIL.Image | url>}
 *
 * so the server decodes rather than forwards:
 *
 *     Image.open(BytesIO(base64.b64decode(capture["data"])))
 *
 * Two rules that are easy to get wrong:
 *
 *  1. **Images go before the text in the turn.** Gemma 4's template is explicit
 *     about this, and our user message is already built that way -- capture and
 *     context parts first, the typed question last -- so preserve that order
 *     rather than appending images at the end.
 *  2. **Never forward the base64 as text.** That shows the model a wall of
 *     characters instead of the page: it answers confidently from nothing, and
 *     every character is billed.
 *
 * Gemma 4 has a configurable visual token budget (70/140/280/560/1120 per image);
 * the high end is what OCR-grade reading of a rate table needs, the low end is for
 * "is this layout broken". Worth setting per tool call rather than globally.
 *
 * Text results (`toolResults[].text`) are the opposite case -- already markdown,
 * and they belong in a text block as-is.
 */
export async function* fetchChat(
  request: ChatRequest,
  { signal }: { signal?: AbortSignal } = {},
): AsyncIterable<ChatChunk> {
  const question = lastUserText(request);
  for await (const event of askStream(
    {
      question,
      session_id: request.sessionId,
      context: request.context,
      captures: request.captures,
      attachments: request.attachments,
      toolResults: request.toolResults,
      // Both were accepted into ChatRequest and then dropped here, so the
      // composer's toggle never left the browser. They travel now.
      think: request.think,
      webSearch: request.webSearch,
      model: request.model,
    },
    signal,
  )) {
    if (event.type === "token" && event.text) {
      yield { type: "text-delta", delta: event.text };
    } else if (event.type === "citation" && event.citation?.cite_url) {
      // The API emits only claim-used web or indexed-document evidence on the
      // live supervisor path. Keep the UI shape small rather than persisting
      // the full retrieved-chunk contract in localStorage.
      yield {
        type: "citation",
        citation: {
          url: event.citation.cite_url,
          title: event.citation.title || undefined,
          bank: event.citation.bank || undefined,
          sourceType: event.citation.source_type || undefined,
        },
      };
    } else if (event.type === "error") {
      yield { type: "error", message: event.detail ?? "The assistant failed to answer." };
    } else if (event.type === "tool_call" && event.tool_call_id && event.tool_name) {
      yield {
        type: "tool-call",
        id: event.tool_call_id,
        name: event.tool_name,
        mode: event.mode ?? undefined,
      };
    } else if (event.type === "done" && event.session_id) {
      request.onSessionId?.(event.session_id);
    }
  }
}

/**
 * What the app talks to: the real FastAPI SSE stream.
 */
export const streamChat = fetchChat;

/** The UI must never present a live agent as a mock response. */
export const IS_MOCK_TRANSPORT = false;
