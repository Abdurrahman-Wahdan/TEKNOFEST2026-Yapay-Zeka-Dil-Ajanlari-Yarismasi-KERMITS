import { contextBundle } from "./context-format";
import type { ChatChunk, ChatRequest } from "./types";

/**
 * The one seam between the chat UI and the agent.
 *
 * The backend is still being built, so `streamChat` currently points at
 * `mockChat`. Everything above this module -- the provider, the renderer, the two
 * surfaces -- is written against the async-iterable contract and not against the
 * mock, so switching to the real thing is the single re-assignment at the bottom
 * of this file.
 *
 * The mock is not decoration. Partial-markdown rendering is the whole reason we
 * pulled in Streamdown, and it cannot be verified against a canned answer that
 * appears all at once: a table only proves the point while it is still missing
 * its last row. So the mock streams in small slices, with a delay, exactly as a
 * token stream would.
 */

/**
 * How the fake stream paces itself.
 *
 * Sized to match a real token stream rather than to look busy. A 4-character
 * slice every 18ms is ~55 updates a second, and every update re-renders the whole
 * answer -- React cannot keep up, the timers slip, and the result is a stream
 * that crawls. A real model emits word-sized chunks a few dozen times a second,
 * which is both truer to what the backend will do and comfortably renderable.
 */
const SLICE = 18;
const DELAY_MS = 40;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** The most recent thing the user actually typed, for the mock to react to. */
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
 * Canned answers, in the app's own domain rather than the generic lorem a
 * component demo ships with. Between them they cover every markdown feature the
 * renderer claims to handle: headings, bold, a GFM table, a fenced code block
 * with a language, a nested list, a blockquote, an inline link.
 *
 * Not translated. These stand in for the agent's output, not for the app's
 * chrome -- the real agent will answer in whatever language it was asked in, and
 * putting fixture prose in `messages/*.json` would imply otherwise.
 */
const ANSWERS = [
  `## Konut finansmanı karşılaştırması

1.000.000 TL, 120 ay vadede bugün itibarıyla **en uygun üç teklif**:

| Banka | Kâr oranı | Aylık taksit | Toplam geri ödeme |
| --- | --- | --- | --- |
| Vakıf Katılım | 2,89% | 28.410 TL | 3.409.200 TL |
| Kuveyt Türk | 2,95% | 28.940 TL | 3.472.800 TL |
| Ziraat Katılım | 3,02% | 29.560 TL | 3.547.200 TL |

> Oranlar bankaların yayınladığı tablolardan alınmıştır ve gün içinde değişebilir.

### Dikkat edilmesi gerekenler

- **Dosya masrafı** üç teklifin hiçbirinde taksite dahil değil.
- Vakıf Katılım'ın oranı yalnızca:
  - maaş müşterileri için,
  - ve 50 yaş altı başvurularda geçerli.
- Kuveyt Türk erken kapamada ceza uygulamıyor.

Bu hesabı kendiniz çalıştırmak isterseniz:

\`\`\`sql
SELECT bank_code,
       profit_rate,
       monthly_payment(1000000, profit_rate, 120) AS taksit
FROM   campaigns
WHERE  product = 'konut'
  AND  valid_until >= CURRENT_DATE
ORDER  BY profit_rate ASC
LIMIT  3;
\`\`\`

Ayrıntılar için [kampanyalar sayfasına](/kampanyalar) bakabilirsiniz.`,

  `## Bu haftanın kampanya değişiklikleri

Geçen haftaya göre **dört** kampanyada hareket var:

| Banka | Ürün | Önceki | Şimdi | Değişim |
| --- | --- | --- | --- | --- |
| Albaraka Türk | Taşıt | 3,45% | 3,19% | ↓ 0,26 |
| Emlak Katılım | Konut | 2,99% | 2,99% | — |
| Kuveyt Türk | İhtiyaç | 4,10% | 4,35% | ↑ 0,25 |
| Ziraat Katılım | Taşıt | 3,60% | 3,28% | ↓ 0,32 |

Taşıt tarafında iki banka aynı hafta indirime gitti; **ihtiyaç** finansmanında ise
tek yönlü bir artış var.

### Süresi dolmak üzere olanlar

1. Albaraka Türk taşıt kampanyası — 6 gün
2. Ziraat Katılım taşıt kampanyası — 11 gün

\`\`\`python
# Değişimi kendiniz izlemek için
changes = diff_campaigns(week_of="2026-08-11", product="tasit")
for c in changes:
    print(f"{c.bank:20} {c.previous:>6} -> {c.current:>6}")
\`\`\`

Not: Emlak Katılım oranını değiştirmedi ama **üst limiti** 2 milyon TL'den
3 milyon TL'ye çıkardı.`,
];

/**
 * The stand-in agent.
 *
 * Echoes which toggles were set before answering, so the composer's Think and
 * Deep Search chips visibly do *something* while the real backend is still
 * ignoring them -- a control with no feedback is indistinguishable from a broken
 * one.
 */
async function* mockChat(
  request: ChatRequest,
  { signal }: { signal?: AbortSignal } = {},
): AsyncIterable<ChatChunk> {
  // A beat before the first token, so `submitted` is a state you can actually
  // see and cancel out of rather than a frame that flickers past.
  await sleep(320);
  if (signal?.aborted) return;

  /**
   * "Look at my screen" -- asked for, then answered.
   *
   * The real agent decides this; the mock pattern-matches, so the whole round trip
   * is exercisable before the backend exists. Without it the tool loop would be
   * code nobody had ever seen run.
   *
   * `read_page` rather than `capture_page` by default, for the same reason the
   * outline exists: it is exact where an image is a guess. The capture is asked
   * for only when the user's wording is about how the page *looks*.
   */
  if (!request.toolResults?.length) {
    const asked = lastUserText(request);
    if (/ekran|screen|bu sayfa|current page|sayfaya bak|what.*(i|I).*see|görüyorum|görün|bozuk|broken/i.test(asked)) {
      // A real agent chooses the mode from the question; the mock reads the same
      // signals so the whole round trip is exercisable before the backend exists.
      // `both` when the wording does not clearly point one way, which is the same
      // default the tool itself uses.
      const visualOnly = /nasıl görünüyor|bozuk|broken|layout|hizal/i.test(asked);
      const dataOnly = /rakam|oran|tutar|sayı|figure|rate|amount/i.test(asked);
      yield {
        type: "tool-call",
        id: "call-1",
        name: "look_at_page",
        mode: visualOnly ? "image" : dataOnly ? "text" : "both",
      };
      return;
    }
  }

  // Second pass: the tools answered, so say what came back.
  if (request.toolResults?.length) {
    const outline = request.toolResults.find((r) => r.text)?.text;
    const shot = request.toolResults.find((r) => r.image);
    // No tool names, no counts. The user is told the assistant looked at the page;
    // which tool ran and what it found is not theirs to read.
    let answer = `Sayfaya baktım.\n\n`;
    if (shot) {
      // Reported as a decodable image block rather than as a blob of characters,
      // which is the whole point of splitting it at the seam.
      answer += `Ekran görüntüsü alındı: ${shot.image!.mediaType}, ${shot.image!.width}×${shot.image!.height}, ${Math.round(
        shot.image!.data.length / 1024,
      )} kB base64.\n\n`;
    }
    if (outline) {
      answer += `### Ajana giden sayfa özeti\n\n\`\`\`xml\n${outline}\n\`\`\`\n`;
    }
    for (let i = 0; i < answer.length; i += SLICE) {
      if (signal?.aborted) return;
      yield { type: "text-delta", delta: answer.slice(i, i + SLICE) };
      await sleep(DELAY_MS);
    }
    return;
  }

  // Alternate, so asking twice does not return the same wall of text and make a
  // working stream look like a cached one.
  const turn = request.messages.filter((m) => m.role === "user").length;
  let answer = ANSWERS[(turn - 1 + ANSWERS.length) % ANSWERS.length];

  // Echoed back so the Think toggle and any attached document visibly reach the
  // request while the real backend is still ignoring both -- a control with no
  // feedback is indistinguishable from a broken one.
  const notes = [
    request.think ? "`think` açık" : null,
    request.attachments?.length
      ? `${request.attachments.length} belge ekli (${request.attachments
          .map((a) => a.filename)
          .join(", ")})`
      : null,
    request.context?.length
      ? `${request.context.length} bağlam ekli (${request.context
          .map((c) => c.label)
          .join(", ")})`
      : null,
    request.captures?.length
      ? `${request.captures.length} ekran görüntüsü ekli (${request.captures
          .map((c) => `${c.label} ${c.mediaType}, ${Math.round(c.data.length / 1024)} kB`)
          .join("; ")})`
      : null,
  ].filter(Boolean);
  if (notes.length > 0) {
    answer = `_${notes.join(" · ")} — arka uç bunları henüz okumuyor._\n\n${answer}`;
  }

  /**
   * Attached context is quoted back verbatim, not just counted.
   *
   * This is the only way to see what the agent would actually be handed while
   * the agent does not exist: whether a bank column serialised as its name or
   * its provider key, whether a pipe in a cell broke the table, whether a 200-row
   * table said so when it was cut. A count would confirm the wire and prove
   * nothing about the payload. It goes away with the mock.
   */
  if (request.context?.length) {
    answer = `${answer}\n\n### Ajana gidecek bağlam\n\n\`\`\`xml\n${contextBundle(
      request.context,
    )}\n\`\`\`\n`;
  }

  for (let i = 0; i < answer.length; i += SLICE) {
    // Checked every slice, not just at the top: `stop` has to land mid-answer to
    // be worth having.
    if (signal?.aborted) return;
    yield { type: "text-delta", delta: answer.slice(i, i + SLICE) };
    await sleep(DELAY_MS);
  }
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
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });

  if (!response.ok || !response.body) {
    yield { type: "error", message: `HTTP ${response.status}` };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Everything up to the last newline is complete; the remainder is a partial
    // line and must stay in the buffer, or a chunk split mid-JSON throws.
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        yield JSON.parse(trimmed) as ChatChunk;
      } catch {
        // A malformed line is the backend's problem, not a reason to kill a
        // conversation that is otherwise streaming fine.
      }
    }
  }
}

/**
 * What the app talks to. Point this at `fetchChat` when the backend is merged.
 */
export const streamChat = mockChat;

/** True while the mock is wired up, so the UI can say so instead of pretending. */
export const IS_MOCK_TRANSPORT = streamChat === mockChat;
