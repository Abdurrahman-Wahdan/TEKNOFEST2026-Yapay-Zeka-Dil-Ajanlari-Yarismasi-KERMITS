/** Repairs small model-authored TeX symbols that do not warrant a math bundle. */

const ARROWS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\$\s*\\+(?:Leftrightarrow|Longleftrightarrow)\s*\$/g, "⇔"],
  [/\$\s*\\+Rightarrow\s*\$/g, "⇒"],
  [/\$\s*\\+Leftarrow\s*\$/g, "⇐"],
  [/\$\s*\\+(?:long)?leftrightarrow\s*\$/g, "↔"],
  [/\$\s*\\+(?:long)?rightarrow\s*\$/g, "→"],
  [/\$\s*\\+(?:long)?leftarrow\s*\$/g, "←"],
  [/\$\s*\\+to\s*\$/g, "→"],
  [/\$\s*\\+uparrow\s*\$/g, "↑"],
  [/\$\s*\\+downarrow\s*\$/g, "↓"],
];

const RELATIONS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\$\s*\\+(?:ge|geq)\s*\$/g, "≥"],
  [/\$\s*\\+(?:le|leq)\s*\$/g, "≤"],
  [/\$\s*\\+(?:ne|neq)\s*\$/g, "≠"],
  [/\$\s*\\+approx\s*\$/g, "≈"],
  [/\$\s*\\+gt\s*\$/g, ">"],
  [/\$\s*\\+lt\s*\$/g, "<"],
];

const UNICODE_ARROW = /[⇔⇒⇐↔→←↑↓]/;

function replaceArrows(text: string): string {
  return ARROWS.reduce(
    (normalised, [pattern, replacement]) => normalised.replace(pattern, replacement),
    text,
  );
}

function replaceSimpleTex(text: string): string {
  return RELATIONS.reduce(
    (normalised, [pattern, replacement]) => normalised.replace(pattern, replacement),
    replaceArrows(text),
  );
}

function isFencedCodeBlock(markdown: string, cursor: number, fence: string): boolean {
  if (fence.length < 3) return false;
  const lineStart = markdown.lastIndexOf("\n", cursor - 1) + 1;
  return /^[ \t]{0,3}$/.test(markdown.slice(lineStart, cursor));
}

function replaceInlineMenuArrows(span: string, fence: string): string {
  const inner = span.slice(fence.length, -fence.length);
  const normalised = replaceArrows(inner);
  if (normalised === inner) return span;

  // A standalone TeX token in code is an example, not prose. A path or compact
  // flow has real content on both sides of its arrow and is the model-authored
  // menu-path case this repair exists for.
  const arrow = normalised.search(UNICODE_ARROW);
  const before = normalised.slice(0, arrow);
  const after = normalised.slice(arrow + 1);
  if (!/[\p{L}\p{N}]/u.test(before) || !/[\p{L}\p{N}]/u.test(after)) return span;

  return `${fence}${normalised}${fence}`;
}

/**
 * Turn simple TeX symbol wrappers into readable Unicode outside Markdown code.
 *
 * Streamdown intentionally loads only its code plugin; pulling in KaTeX for a
 * menu path such as `Mobil Şube → Hesap` or a table threshold such as `≥10k`
 * would add a large dependency for one symbol. Models nevertheless sometimes
 * emit `$\rightarrow$` and `$\ge$`. Repairing that narrow, deterministic set
 * keeps prose readable while leaving currency, formulas, standalone symbol
 * code, and fenced examples untouched. Menu paths are often wrapped in an
 * inline code span, so arrows there are repaired only when there is text on
 * both sides.
 */
export function normaliseAgentMarkdown(markdown: string): string {
  let output = "";
  let textStart = 0;
  let cursor = 0;

  while (cursor < markdown.length) {
    if (markdown[cursor] !== "`") {
      cursor += 1;
      continue;
    }

    let fenceEnd = cursor + 1;
    while (markdown[fenceEnd] === "`") fenceEnd += 1;
    const fence = markdown.slice(cursor, fenceEnd);
    const closing = markdown.indexOf(fence, fenceEnd);
    if (closing < 0) break;

    output += replaceSimpleTex(markdown.slice(textStart, cursor));
    const span = markdown.slice(cursor, closing + fence.length);
    output += isFencedCodeBlock(markdown, cursor, fence)
      ? span
      : replaceInlineMenuArrows(span, fence);
    cursor = closing + fence.length;
    textStart = cursor;
  }

  return output + replaceSimpleTex(markdown.slice(textStart));
}
