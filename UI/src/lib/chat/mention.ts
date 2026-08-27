/** Locate the open @file query at the textarea caret. */
export function mentionAt(
  value: string,
  caret: number,
): { start: number; query: string } | null {
  if (caret < 0) return null;

  // Filenames commonly contain spaces, so scanning only to the nearest
  // whitespace makes `@bank statement.pdf` stop working after `bank`. Use the
  // latest word-boundary @ on the current line instead.
  const lineStart = value.lastIndexOf("\n", Math.max(caret - 1, 0)) + 1;
  const start = value.lastIndexOf("@", caret - 1);
  if (start < lineStart) return null;
  if (start > 0 && !/\s/.test(value[start - 1])) return null;

  const query = value.slice(start + 1, caret);
  // A selected token is `@[filename]`; once closed it is ordinary composer
  // text, not another open mention. Newlines are excluded by lineStart above.
  if (query.includes("[") || query.includes("]")) return null;
  return { start, query };
}
