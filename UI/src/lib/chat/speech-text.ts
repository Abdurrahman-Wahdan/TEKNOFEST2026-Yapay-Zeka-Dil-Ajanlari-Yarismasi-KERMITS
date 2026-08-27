/**
 * Turning an agent answer into something a synthesiser can read.
 *
 * Apart from `speech.ts` because it is the half worth testing and the half with
 * no browser in it: the hook next door owns the utterance queue and cannot be
 * loaded outside one, while everything here is a string going in and a string
 * coming out.
 */

/**
 * Roughly how much text goes into one utterance.
 *
 * Not a cosmetic choice. Chrome stops mid-sentence on a long utterance -- a
 * watchdog fires after about fifteen seconds and the rest is simply never
 * spoken -- so a whole comparison answer handed over in one piece dies partway
 * through with no error. Short utterances queued back to back stay under it, and
 * draining a queue is what the platform is built to do.
 */
export const CHUNK_CHARS = 180;

/**
 * Markdown, as something worth hearing.
 *
 * Not a general-purpose markdown stripper: what it has to be right about is the
 * shape of *this* agent's answers. Two decisions carry that.
 *
 * **Tables are read, not skipped.** They are usually the entire answer here --
 * ten banks against five columns -- and dropping them would leave the voice
 * saying "here is the comparison" and then stopping. Each row is spoken as
 * "column: value", which is how a person reads a table aloud and, not
 * coincidentally, the encoding this app already found answers best.
 *
 * **Code blocks are dropped.** A fenced block read character by character is
 * unlistenable, and nothing in a banking answer depends on hearing one.
 */
export function speakableText(markdown: string): string {
  const spoken: string[] = [];
  /** The most recent table's headers, so its rows can be read against them. */
  let headers: string[] | null = null;
  /** The fence that opened the code block being skipped, if one is open. */
  let fence: string | null = null;

  for (const raw of markdown.split("\n")) {
    const line = raw.trim();
    const marker = line.match(/^(`{3,}|~{3,})/)?.[1];

    if (fence !== null) {
      // Only a fence of the same character and at least the same length closes
      // it -- ``` inside a ~~~~ block is content, not the end of the block. An
      // unterminated one swallows the rest, which is exactly right while an
      // answer is still streaming: half a code block is not something to read.
      if (marker && marker[0] === fence[0] && marker.length >= fence.length && line === marker) {
        fence = null;
      }
      continue;
    }

    if (marker) {
      fence = marker;
      headers = null;
      continue;
    }

    if (!line) {
      headers = null;
      continue;
    }

    // A rule is a visual break with nothing to say.
    if (/^([-*_])(\s*\1){2,}$/.test(line)) {
      headers = null;
      continue;
    }

    if (isTableRow(line)) {
      const cells = tableCells(line);
      // The `|---|:--:|` separator is punctuation for the eye only, and it is
      // also the proof that the line above it was a header rather than a row.
      if (cells.every((cell) => /^:?-+:?$/.test(cell))) continue;
      if (headers === null) {
        headers = cells;
        continue;
      }
      const pairs = cells.map((cell, index) => {
        const header = headers?.[index]?.trim();
        // An em dash, not "", so a blank cell is heard as a gap in the table
        // rather than as the next column's name arriving early.
        const value = inlineText(cell) || "—";
        return header ? `${inlineText(header)}: ${value}` : value;
      });
      spoken.push(sentence(pairs.join(", ")));
      continue;
    }

    headers = null;

    // A heading is a sentence in its own right. The full stop is what makes the
    // synthesiser pause after it instead of running it into the paragraph below.
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) {
      spoken.push(sentence(inlineText(heading[1])));
      continue;
    }

    const listItem = line.match(/^(?:[-*+]|\d+[.)])\s+(.*)$/);
    if (listItem) {
      spoken.push(sentence(inlineText(listItem[1])));
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    spoken.push(inlineText(quote ? quote[1] : line));
  }

  return spoken
    .map((line) => line.trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/** A pipe table's row, and not a sentence that happens to contain a bar. */
function isTableRow(line: string): boolean {
  return line.startsWith("|") && line.slice(1).includes("|");
}

function tableCells(line: string): string[] {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split(/(?<!\\)\|/)
    .map((cell) => cell.trim());
}

/** End a fragment so the synthesiser gives it the pause a sentence gets. */
function sentence(text: string): string {
  if (!text) return "";
  return /[.!?:;…]$/.test(text) ? text : `${text}.`;
}

/** Strip the marks that carry emphasis to the eye and nothing to the ear. */
function inlineText(text: string): string {
  return text
    // An image is its alt text or nothing; a link is what it was written as.
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    // Inline code keeps its contents -- `%2,89` is the number being asked about.
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\*\*\*([^*]+)\*\*\*/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(?<![\p{L}\p{N}])_{1,2}([^_]+)_{1,2}(?![\p{L}\p{N}])/gu, "$1")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1")
    .replace(/<[^>]+>/g, "")
    .replace(/\\([\\`*_{}[\]()#+\-.!|])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/**
 * Where one sentence ends, in a language that writes 3.031.200 for three million.
 *
 * A full stop only ends a sentence when whitespace or the end of the text
 * follows it. Turkish groups thousands with full stops and writes dates as
 * 27.08.2026, and this app's answers are made of both -- splitting on every dot
 * cut "3.031.200 TL" into "3.031." and "200 TL", which the synthesiser reads as
 * two sentences with a pause down the middle of the number.
 */
function splitSentences(text: string): string[] {
  const pieces: string[] = [];
  const terminator = /[.!?…]+/g;
  let start = 0;
  let match: RegExpExecArray | null;

  while ((match = terminator.exec(text)) !== null) {
    const end = match.index + match[0].length;
    const next = text[end];
    if (next !== undefined && !/\s/.test(next)) continue;
    pieces.push(text.slice(start, end));
    start = end;
  }
  // Whatever follows the last terminator, or the whole text when there is none.
  if (start < text.length) pieces.push(text.slice(start));
  return pieces;
}

/**
 * Cut an answer into utterance-sized pieces, on sentence boundaries.
 *
 * Splitting mid-sentence would put a hard stop where the prose has none, which
 * is audible. A sentence longer than the budget is spoken whole rather than cut:
 * a wrong pause is worse than a long utterance, and the fifteen-second watchdog
 * this exists for takes many sentences to trip, not one.
 *
 * Lossless when rejoined with a single space, which is what makes it safe to
 * cut the text at all: `speakableText` has already collapsed every whitespace
 * run to one space, so the separators the pieces carry are exactly recoverable.
 */
export function speechChunks(text: string, size: number = CHUNK_CHARS): string[] {
  const chunks: string[] = [];
  let current = "";

  for (const piece of splitSentences(text)) {
    const next = current ? `${current}${piece}` : piece;
    if (current && next.trim().length > size) {
      chunks.push(current.trim());
      current = piece;
      continue;
    }
    current = next;
  }
  if (current.trim()) chunks.push(current.trim());
  return chunks;
}
