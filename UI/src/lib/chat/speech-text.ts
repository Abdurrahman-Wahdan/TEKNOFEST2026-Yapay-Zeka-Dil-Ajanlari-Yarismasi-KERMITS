/**
 * Turning an agent answer into something worth reading aloud.
 *
 * Apart from `speech.ts` because it is the half worth testing and the half with
 * no browser in it: the hook next door owns an audio graph and a fetch and
 * cannot be loaded outside a browser, while everything here is a string going in
 * and a string coming out.
 *
 * This stays on the client even though the voice is now the server's. The server
 * is handed prose and asked to say it; deciding that a table should be read
 * "column: value" and that a code block should not be read at all is a question
 * about *this* app's answers, and the API has no business knowing it. Cutting
 * that prose into model-sized pieces is the server's job and lives in
 * `api/voice_speech.py`.
 */

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
