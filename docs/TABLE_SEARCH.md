# Keyword search over the comparison-table pool

Measured, not chosen by taste: every figure below came from running the candidate
algorithms against the **real pool of 403 tables** (`GET /api/compare-tables`, 260
ürün + 143 kampanya) on **2026-08-22**, over 1,480 generated queries whose correct
answer is known.

This exists because `/urunler` and `/kampanyalar` are a grid of 260 cards behind a
subcategory dropdown. Scrolling that grid is not browsing, it is looking for one
table by name — so the search box is the primary way in, and how well it matches is
the whole feature. The first version shipped that morning missed the table the user
was aiming at **18% of the time**.

Implementation: [`UI/src/lib/table-search.ts`](../UI/src/lib/table-search.ts).
Tests: [`UI/src/lib/table-search.test.ts`](../UI/src/lib/table-search.test.ts).
UI: [`UI/src/components/ui/SearchField.tsx`](../UI/src/components/ui/SearchField.tsx).

---

## 1. What is being searched

Each table summary carries three fields, all Turkish, all written by the offline
producer (`dataprep.compare`):

| field | example | length |
|---|---|---|
| `topic` | `Konut Finansmanı` | 2–8 words |
| `subcategory` | `murabaha finansman` | 1–4 words |
| `docstring` | `Yeni ve ikinci el konut alımı için sunulan kâr oranları…` | one sentence |

Two properties of the data drive everything below:

- **It is Turkish and it is inflected.** The producer writes `finansmanı`; nobody
  types the suffix. `oranı`, `oranları` and `oranlarında` are one word wearing three
  suffixes, and none is a prefix of another.
- **The user's keyboard may not be.** `İşyeri` gets typed `isyeri`, `kâr` as `kar`.

## 2. Method

### Query generation

Ground truth is the table a query was derived from. Two disjoint sets, so the
parameters cannot be scored on the queries that chose them:

**Tuning set** — every 3rd table (offset 0), 810 queries in six shapes:

| shape | how it is built | stands for |
|---|---|---|
| `exact` | first two content words of the title | typing the name |
| `caps` | the same, `toLocaleUpperCase("tr")` | caps lock / phone autocaps |
| `stem` | the same with the last 2 characters of long words dropped | the suffix nobody types |
| `ascii` | the same with ı ş ğ ü ö ç â folded to ASCII | a non-Turkish keyboard |
| `typo` | one character deleted from the longest word | a typo |
| `docword` | one title word + one distinctive word from the docstring | describing, not naming |

**Held-out set** — every 3rd table (offset 1), 670 queries in five *different*
shapes, using different mistakes: the **last** two title words, a single word, an
adjacent-character **transposition**, subcategory + title mix, and a truncated
second word. Nothing here was looked at while choosing parameters.

Stopwords in generation only: `ve`, `ile`, `için`, `bir`, `özel`, `veya`.

### Metrics

- **hit@1** — the intended table is the first result. This is what the user sees.
- **hit@5** — it is in the first row and a half of the grid.
- **MRR** — mean reciprocal rank; degrades gracefully rather than as a cliff.
- **missed** — it is not in the results at all. The number that matters most: a
  ranking mistake costs a glance, a miss costs the feature.
- **results** — mean result-set size. A search that returns everything never misses
  and never helps, so this is read alongside the others.

## 3. Candidates

Tuning set, 810 queries, 403 tables:

| algorithm | hit@1 | hit@5 | MRR | missed | results |
|---|---|---|---|---|---|
| A — Turkish fold, AND substring *(first shipped)* | 53.6% | 75.1% | 62.7 | 18.4% | 4.2 |
| B — A + diacritic folding | 61.1% | 85.3% | 71.3 | 6.8% | 5.0 |
| **C — tiers + field weights + typo tolerance** | **79.1%** | **94.6%** | **85.6** | **0.5%** | 8.0 |
| D — C without typo tolerance | 73.6% | 88.3% | 79.8 | 6.8% | 6.2 |
| E — trigram Dice similarity | 76.4% | 87.3% | 81.5 | 2.0% | 26.0 |
| F — BM25 | 74.2% | 91.1% | 81.6 | 3.5% | 32.4 |

Recall by query shape, which is where A's 18% of misses actually live:

| algorithm | exact | caps | stem | ascii | typo | docword |
|---|---|---|---|---|---|---|
| A | 100% | 100% | 100% | **30%** | **59%** | 100% |
| B | 100% | 100% | 100% | 100% | **59%** | 100% |
| C | 100% | 100% | 100% | 100% | 97% | 100% |
| E | 100% | 100% | 100% | 100% | 100% | 88% |
| F | 100% | 100% | **79%** | 100% | 100% | 100% |

**Why not BM25 or trigrams.** Both rank respectably and neither can be filtered:
they return 26 and 32 tables where C returns 8, because both score partial matches
instead of requiring every word. A ranked list with a long tail works when the UI
is ten blue links with a fold; a picker grid has no fold and no relevance cutoff,
so everything returned is drawn at equal weight. BM25 additionally loses a fifth of
stem queries — an IDF model has no notion that `finansman` and `finansmanı` are the
same term, and adding one means shipping a Turkish stemmer.

## 4. Tuning C

| variant | hit@1 | hit@5 | MRR | missed |
|---|---|---|---|---|
| base — weights 6/3/1 | 79.1% | 94.6% | 85.6 | 0.5% |
| weights 4/2/1 | 79.3% | 94.6% | 85.7 | 0.5% |
| weights 10/2/1 | 79.0% | 94.6% | 85.6 | 0.5% |
| + phrase bonus | 79.6% | 94.7% | 85.9 | 0.5% |
| + shortest-title tie-break | 83.7% | 95.2% | 88.3 | 0.5% |
| + both | 84.0% | 95.3% | 88.5 | 0.5% |
| typo tolerance from 4 characters | 79.4% | 94.9% | 85.9 | **0.0%** |
| typo tolerance from 6 characters | 78.9% | 94.3% | 85.4 | 0.7% |
| **all of the above together** | **84.4%** | **95.9%** | **89.0** | **0.0%** |

Field weights barely move the numbers; the **tie-break does**. Ties are common
because most queries are two words that either match a title or do not, and with
equal evidence the shorter title is the better answer — `Konut Finansmanı` over
`Konut Sigortası Yenileme Kampanyası` for "konut". That one comparison is worth
4 points of hit@1.

## 5. Ablation, on the held-out set

Each row removes one part from the winner. The held-out set is harder by
construction (single generic words legitimately match many tables), so read the
columns against each other, not against §4:

| variant | hit@1 | hit@5 | MRR | missed |
|---|---|---|---|---|
| **winner** | **59.0%** | **78.4%** | **68.1** | **4.0%** |
| − phrase bonus | 58.1% | 78.2% | 67.6 | 4.0% |
| − shortest-title tie-break | 57.8% | 78.2% | 67.3 | 4.0% |
| − field weights | 56.1% | 76.6% | 65.7 | 4.0% |
| − typo tolerance | 48.8% | 66.3% | 57.1 | 16.3% |
| − everything (plain AND substring) | 37.3% | 60.6% | 47.6 | 16.3% |

Every part earns its place, and typo tolerance earns the most: it is the difference
between 4% and 16% of queries finding nothing at all.

## 6. Where the typo pass runs

Edit distance against every token in the pool is 90% of a keystroke. Three ways to
spend less, measured:

| | tuning hit@1 / MRR / missed | held-out hit@1 / MRR / missed | per keystroke |
|---|---|---|---|
| typo pass on all three fields | 84.4% / 89.0 / 0.0% | 59.0% / 68.1 / 4.0% | 4.35 ms |
| **typo pass on title + subcategory only** | **84.4% / 89.0 / 0.0%** | **59.0% / 68.1 / 4.0%** | **1.47 ms** |
| typo pass only when the strict pass came back empty | 84.2% / 88.7 / 0.6% | 58.4% / 67.3 / 5.2% | 0.36 ms |
| + shared-prefix tier (≥ 4 chars) | 84.6% / 89.1 / 0.0% | 59.0% / 68.1 / 4.0% | 1.54 ms |
| + shared-prefix tier (≥ 3 chars) | 84.2% / 88.9 / 0.0% | 58.8% / 68.0 / 4.0% | 1.59 ms |

Two findings here:

- **Dropping the docstring from the typo pass is free.** It holds most of the pool's
  tokens and contributed nothing a title or subcategory match did not already find.
  Same numbers, a third of the time.
- **Deferring the typo pass until the strict pass is empty is not free**, though it
  is tempting at 12× faster. It costs 1.2 points of miss rate on the held-out set,
  because a typo'd word can be the *second* word of a query whose first word matched
  several tables strictly — the strict pass returns something, just not the right
  thing, so the rescue never runs. Rejected.

The **shared-prefix tier** came out of a failing unit test rather than the sweep:
`oranı` against `oranları` is neither prefix nor substring, and was only matching
through the docstring's typo pass. Treating ≥ 4 agreeing characters as inflection
handles Turkish agglutination in every field for free. Three characters is too few —
unrelated words start colliding and both sets get worse.

## 7. What shipped

```
score(table, query) =
    Σ over query words:  max over fields:  tier(word, field) × weight(field)
  + 8 if the whole folded query appears in the title, in order
  = 0 if any query word matches nothing            ← AND, not OR
```

**Folding.** `fold()` (Turkish-safe lowercase), then NFD, then strip combining
marks, then `ı → i`. NFD covers ş ğ ü ö ç â in one pass *and* the combining dot
`fold` leaves on `İ` when the UI locale is English — so what matches does not depend
on the interface language agreeing with the data's language. `ı` has no
decomposition and is spelled out. `fold()` itself stays the general-purpose folder:
stripping accents everywhere would make two different bank names compare equal.

**Tiers**, best match per field: exact token `1.0` · prefix either way round `0.85` ·
substring `0.6` · crosses a token boundary `0.6` · shared prefix ≥ 4 `0.5` · within
edit distance `0.4` · typo against the token's prefix `0.35`.

The prefix tier is why no stemmer is needed: "one of these two words starts with the
other" carries the common suffixes, and a stemmer would be a second place for the
language to be wrong.

**Weights** — title `8`, subcategory `3`, docstring `1`. **Typo slack** — 1 edit
under 7 characters, 2 at or above, and none below 4 where a typo is indistinguishable
from a different short word. **Ties** — shorter title first, then locale collation.

**Index** — folded and tokenised once per list and cached on the array reference
(`WeakMap`), because the component memoises its subcategory slice. Building it is
6.3 ms; searching is 1.5 ms. Rebuilding per keystroke would be 97% of the work.

Final numbers, the module as shipped:

| | hit@1 | hit@5 | MRR | missed | results |
|---|---|---|---|---|---|
| tuning, before | 53.6% | 75.1% | 62.7 | 18.4% | 4.2 |
| **tuning, after** | **84.6%** | **95.8%** | **89.1** | **0.0%** | 9.0 |
| held-out, before | 24.3% | 43.0% | 32.9 | 33.0% | 16.2 |
| **held-out, after** | **59.0%** | **78.4%** | **68.1** | **4.0%** | 30.1 |

Latency on 403 tables, after the index is warm: **1.65 ms median** per keystroke
(p95 2.21 ms), 1.43 ms for a query that matches nothing.

## 8. Limits

- **No synonyms.** "ev kredisi" does not find "Konut Finansmanı". That needs a
  domain lexicon, which is data, not an algorithm — and it is the obvious next gain.
- **Scoring is linear in the pool.** Fine at 403 tables and ~1.5 ms; an inverted
  index only becomes worth its complexity in the thousands.
- **The held-out numbers are not a ceiling on user satisfaction.** Its single-word
  queries ("finansmanı") match dozens of tables *correctly* — hit@1 is unfair to
  them, which is why misses and MRR are quoted beside it.
- **Query shapes are generated, not sampled.** They cover how people mistype, not
  what people actually search for. Real query logs would beat all of this; there
  are none yet.

## 9. Reproducing

The harness is not committed — it was a scratch experiment. Everything needed to
rebuild it is above: fetch both categories from `/api/compare-tables`, generate the
two query sets by the rules in §2, and score `searchTables` from
`UI/src/lib/table-search.ts` against the known target. Ask before re-tuning on a
grown pool, not after: the constants in §7 are cheap to re-sweep and expensive to
guess.

The behaviours the numbers bought are pinned as unit tests — folding, edit distance,
inflection, typos, ranking order, index staleness:

```bash
cd UI && npm test
```
