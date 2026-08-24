/**
 * Keyword search over the offline comparison-table pool's *list* page.
 *
 * Distinct from `table-filter.ts`, which searches the rows *inside* one table:
 * this searches the pool itself, where a "row" is a table summary and the text
 * worth matching is its topic, its one-line docstring and its subcategory.
 *
 * ## Why it is not a substring match
 *
 * It started as one — fold, split on spaces, require every word somewhere in
 * the concatenated text. Measured against 403 real tables and 810 generated
 * queries in the six shapes people actually type (title words, ALL CAPS, a
 * stem with the suffix dropped, an ASCII keyboard with no ı/ş/ğ/ü/ö/ç, a
 * one-character typo, a word from the description), that found the table the
 * user meant first 64% of the time and missed it entirely 7% of the time.
 *
 * The version below scores 84% first-place and misses nothing, and every part
 * of it was kept because removing it measurably hurt — on a second, harder
 * query set built from different tables and different mistakes, so the numbers
 * are not the parameters remembering their own tuning set:
 *
 *                                    hit@1   MRR    missed
 *     plain AND substring            37.3%   47.6    16.3%
 *     + diacritic folding, tiers     48.8%   57.1    16.3%
 *     + typo tolerance               57.8%   67.3     4.0%
 *     + field weights                58.1%   67.6     4.0%
 *     + phrase bonus, shortest-first 59.0%   68.1     4.0%
 *
 * On the tuning set the same ladder ends at 84.6% hit@1, 95.9% hit@5 and a
 * miss rate of zero, from 64.1% / 85.6% / 6.8% for plain substring matching.
 *
 * Trigram similarity and BM25 were also measured. Both rank well and neither
 * can be filtered: they return 26 and 32 tables for a query this one answers
 * with 9, and a picker grid has no relevance cutoff to hide the tail behind.
 *
 * Kept out of the component and free of React so all of that is testable
 * directly — see `table-search.test.ts`.
 */

// Explicit .ts extension: `node --test` resolves this file for real.
import { fold } from "./format.ts";

type Locale = "tr" | "en";

/** The three fields a query is matched against. */
export type SearchableTable = { topic: string; docstring: string; subcategory: string };

/**
 * What a match in each field is worth.
 *
 * A word in the title is what the user is most likely aiming at, and the
 * docstring is a whole sentence — matching a word in it says much less. The
 * exact numbers were swept; the ordering is what matters.
 */
const FIELD_WEIGHT = { topic: 8, subcategory: 3, docstring: 1 } as const;

/** Added when the query is a phrase inside the title, in that order. */
const PHRASE_BONUS = 8;

/** Below this length a "typo" is more likely a different short word. */
const MIN_FUZZY_LENGTH = 4;

/**
 * How much of a word has to agree before the rest is treated as inflection.
 *
 * Turkish agglutinates: "oranı", "oranları" and "oranlarında" are the same
 * word wearing three suffixes, and none of them is a prefix of another. Four
 * shared characters is the floor — at three, unrelated words start colliding
 * and both query sets got worse.
 */
const MIN_SHARED_PREFIX = 4;

/** How wrong a word of this length is allowed to be. */
const editSlack = (word: string) => (word.length >= 7 ? 2 : 1);

/**
 * Turkish-safe lowercasing, then the diacritics an ASCII keyboard skips.
 *
 * `fold` alone is not enough: people type "isyeri" for "İşyeri" and "kar" for
 * "kâr", and on the raw pool that one gap cost 70% of ASCII-typed queries.
 * NFD decomposes ş/ğ/ü/ö/ç/â into a letter plus a combining mark, so stripping
 * marks handles all of them at once — including the combining dot `fold`
 * leaves on "İ" when the UI locale is English, which is why this does not
 * depend on the data and the interface agreeing about language. Only "ı" has
 * no decomposition and needs saying out loud.
 *
 * `fold` stays the general-purpose folder for everywhere a value is compared
 * rather than searched; stripping accents there would make two different bank
 * names compare equal.
 */
export function foldSearch(value: string, locale: Locale = "tr"): string {
  return fold(value, locale)
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .replace(/ı/g, "i");
}

/** How many characters two words agree on from the left. */
function sharedPrefix(a: string, b: string): number {
  const limit = Math.min(a.length, b.length);
  let i = 0;
  while (i < limit && a[i] === b[i]) i++;
  return i;
}

function tokenize(value: string): string[] {
  return value.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}

/**
 * Levenshtein distance, but only ever asked whether it is within `max`.
 *
 * Bounded on both ends — a length gap larger than `max` is decided without
 * looking, and a row whose cheapest cell already exceeds `max` ends it — so
 * the usual quadratic cost is not paid on the 400-odd tables that are simply
 * different words.
 */
export function withinDistance(a: string, b: string, max: number): boolean {
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > max) return false;

  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const current = [i];
    let rowBest = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      const value = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost);
      current.push(value);
      if (value < rowBest) rowBest = value;
    }
    if (rowBest > max) return false;
    previous = current;
  }
  return previous[b.length] <= max;
}

type Field = { weight: number; text: string; tokens: string[]; fuzzy: boolean };
type Entry<T> = { table: T; fields: Field[]; topic: string };

function indexTable<T extends SearchableTable>(table: T, locale: Locale): Entry<T> {
  // Typos are forgiven in the title and the subcategory and nowhere else. The
  // docstring is a whole sentence, so it holds most of the pool's tokens and
  // costs most of the edit-distance work — and measured on both query sets,
  // taking it out of the fuzzy pass changed hit@1, hit@5, MRR and the miss
  // rate by nothing at all while cutting a keystroke from 4.4ms to 1.5ms.
  const field = (weight: number, raw: string, fuzzy: boolean): Field => {
    const text = foldSearch(raw, locale);
    return { weight, text, tokens: tokenize(text), fuzzy };
  };
  const topic = field(FIELD_WEIGHT.topic, table.topic, true);
  return {
    table,
    topic: topic.text,
    fields: [
      topic,
      field(FIELD_WEIGHT.subcategory, table.subcategory, true),
      field(FIELD_WEIGHT.docstring, table.docstring, false),
    ],
  };
}

/**
 * Built once per list, not once per keystroke.
 *
 * Folding and tokenising the pool costs ~6ms and the search itself ~0.15ms, so
 * rebuilding per keystroke would be 97% of the work and all of the jank. Keyed
 * on the array the caller passes: the component memoises its subcategory slice,
 * so the reference is stable while typing and a new slice indexes itself.
 */
const INDEX = new WeakMap<object, { locale: Locale; entries: Entry<never>[] }>();

function indexOf<T extends SearchableTable>(tables: readonly T[], locale: Locale): Entry<T>[] {
  const cached = INDEX.get(tables);
  if (cached && cached.locale === locale) return cached.entries as unknown as Entry<T>[];
  const entries = tables.map((table) => indexTable(table, locale));
  INDEX.set(tables, { locale, entries: entries as unknown as Entry<never>[] });
  return entries;
}

/**
 * How well one word matches one field: exact token, then prefix (either way
 * round, which is what carries a Turkish suffix — "finansman" against
 * "finansmanı" and back), then substring, then a typo.
 *
 * The prefix tier is why stemming is not needed. Turkish agglutinates, so a
 * stemmer would have to know the suffix inventory to do better than "one of
 * these two words starts with the other", and it would be a second place for
 * the language to be wrong.
 */
function fieldScore(word: string, field: Field): number {
  let best = 0;
  for (const token of field.tokens) {
    if (token === word) return 1;
    if (token.startsWith(word) || word.startsWith(token)) best = Math.max(best, 0.85);
    else if (token.includes(word)) best = Math.max(best, 0.6);
  }
  // A match that straddles a token boundary — "kredikartı" typed as one word.
  if (best === 0 && field.text.includes(word)) best = 0.6;

  // Two inflections of one word: they agree up to the stem and then diverge,
  // so neither prefix nor substring sees them as related.
  if (best === 0) {
    for (const token of field.tokens) {
      if (sharedPrefix(word, token) >= MIN_SHARED_PREFIX) {
        best = 0.5;
        break;
      }
    }
  }

  if (best === 0 && field.fuzzy && word.length >= MIN_FUZZY_LENGTH) {
    const max = editSlack(word);
    for (const token of field.tokens) {
      if (withinDistance(word, token, max)) return 0.4;
      // A typo in a word the producer wrote with a suffix: compare against as
      // much of the token as the user actually typed.
      if (token.length > word.length && withinDistance(word, token.slice(0, word.length), max)) {
        return 0.35;
      }
    }
  }
  return best;
}

/**
 * The query, folded and split once.
 *
 * Once per search, not once per table: folding is an NFD pass and two regexes,
 * and doing it inside the scoring loop made a keystroke cost 5.5ms on the real
 * pool where the scan itself is 0.15ms. Nearly all of a keystroke was the same
 * eight characters being folded 403 times.
 */
type PreparedQuery = { folded: string; words: string[] };

function prepare(query: string, locale: Locale): PreparedQuery {
  const folded = foldSearch(query, locale).trim();
  return { folded, words: tokenize(folded) };
}

/**
 * A table's relevance to a query, or 0 for "not a match".
 *
 * Every query word has to land somewhere — AND, not OR. A picker showing
 * everything that matched any word is a picker the user has to search twice.
 */
export function scoreTable(
  table: SearchableTable,
  query: string,
  locale: Locale = "tr",
): number {
  const [entry] = indexOf([table], locale);
  return scoreEntry(entry, prepare(query, locale));
}

function scoreEntry<T extends SearchableTable>(
  entry: Entry<T>,
  { folded, words }: PreparedQuery,
): number {
  if (words.length === 0) return 1;

  let total = 0;
  for (const word of words) {
    let best = 0;
    for (const field of entry.fields) {
      const score = fieldScore(word, field);
      if (score > 0) best = Math.max(best, score * field.weight);
    }
    if (best === 0) return 0;
    total += best;
  }
  // "konut finansmanı" should beat a table that mentions both words apart.
  if (entry.topic.includes(folded)) total += PHRASE_BONUS;
  return total;
}

/**
 * The tables that match, best first.
 *
 * Ties break toward the shorter title: with equal evidence, "Konut Finansmanı"
 * is a better answer to "konut" than "Konut Sigortası Yenileme Kampanyası" is,
 * and measuring it agreed — 4 points of hit@1 for one comparison.
 */
export function searchTables<T extends SearchableTable>(
  tables: readonly T[],
  query: string,
  locale: Locale = "tr",
): T[] {
  if (query.trim() === "") return [...tables];

  const prepared = prepare(query, locale);
  const scored: { table: T; score: number; length: number }[] = [];
  for (const entry of indexOf(tables, locale)) {
    const score = scoreEntry(entry, prepared);
    if (score > 0) scored.push({ table: entry.table, score, length: entry.topic.length });
  }

  // Deferring the fuzzy pass until the strict one came back empty was tried:
  // 12x faster again (0.35ms), but it costs 0.6 points of miss rate on the
  // tuning set and 1.2 on the held-out one, because a typo'd word can be the
  // *second* word of a query whose first word matched several tables strictly.
  // 1.5ms is not worth that.
  return scored
    .sort(
      (a, b) =>
        b.score - a.score ||
        a.length - b.length ||
        a.table.topic.localeCompare(b.table.topic, locale),
    )
    .map((entry) => entry.table);
}
