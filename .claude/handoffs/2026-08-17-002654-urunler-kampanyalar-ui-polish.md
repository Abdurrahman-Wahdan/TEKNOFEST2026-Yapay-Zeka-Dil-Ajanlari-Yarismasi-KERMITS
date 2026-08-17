# Handoff: Polish the Ürünler / Kampanyalar comparison-table pages

## Session Metadata
- Created: 2026-08-17 00:26:54
- Project: /Users/abdelrahmanwahdan/Desktop/TF26
- Branch: abdelrahman
- Session duration: ~long session, spanning embedding pipeline cleanup, data quality audit, subcategory consolidation, and this UI build

### Recent Commits (for context)
- fcd1b8b feat: consolidate subcategories and enhance handling of subcategory examples in processing functions
- 3aad9d1 feat: enhance campaign status handling in search and retrieval functions
- 480caf3 Merge pull request #12 from Abdurrahman-Wahdan/abdelrahman
- 561f2c2 Merge remote-tracking branch 'origin/main' into abdelrahman
- eea39d6 feat: add derived field to card installment quotes and update calculations for Türkiye Finans

**Note:** the work in this handoff (the two new pages) is NOT yet committed. Check `git status` before doing anything destructive.

## Handoff Chain

- **Continues from**: None — this is a new, independent feature area. (The scaffold auto-linked the most recent handoff, `2026-08-15-173632-fx-rates-board-live.md`, about the live FX Rates board; that work is unrelated to this one and can be ignored for this task.)
- **Supersedes**: None.

## Current State Summary

Two new pages exist and work end-to-end but are visually **unstyled/minimal** — built for correctness first, not polish. `/urunler` and `/kampanyalar` each browse a pool of pre-built cross-bank comparison tables (309 total, split 249 ürün / 60 kampanya) that a separate offline pipeline (`dataprep.compare`) produces from crawled bank sites. The flow per page: pick a subcategory from a dropdown → click a table card from a grid → view that table's full data via the app's existing generic table renderer (`TableWidget`). The next agent's job is to make this look and feel good — the plumbing (backend endpoints, types, data mapping) is done and tested; nothing here should require backend changes unless UI polish reveals a real data gap.

## Codebase Understanding

### Architecture Overview

**Where the table data actually lives**: `data/_tables/*.json` (309 files, one per comparison table). Each has `{id, topic, docstring, category, subcategory, columns, rows, sources}` where `category` is a hard-locked 2-value enum (`"ürün"` | `"kampanya"`, enforced by the LLM prompt in `dataprep/compare/synth.py`) and `subcategory` is free text (57 distinct values after this session's consolidation pass, e.g. `"sigorta ve emeklilik"`, `"ödeme hizmetleri"`). `rows` is `{bank_slug: {column_name: value}}` — the bank is a dict key, not a cell, which the backend converts (see below). This whole pool is built by an agentic traversal (`dataprep/compare/pipeline.py`) that reads `data/<bank>_site/` pages, is unrelated to the app's live bank-endpoint code, and gets embedded separately into Qdrant's `compare_tables` collection for a table-matching tool (`search_tables`) — that embedding side is irrelevant to the UI work.

**Why 2 pages, not integrated into the existing 9-category topic-page system**: the backend already has a *different*, mostly-unbuilt system (`api/routers/components.py::CATEGORIES` — `finansman`, `kartlar`, `kampanyalar`, `doviz-altin`, `yatirim`, `sigorta`, `ucretler`, `dijital`, `subeler`, only `finansman` has a page built) for AI-produced RAG content. The user explicitly decided (mid-session) to build Ürünler/Kampanyalar as two **separate, independent pages instead**, calling the existing 9-category scaffolding "hypothetical structure that will need to be cleaned" later. **Do not** try to merge this feature into that other system unless the user asks — it was a deliberate choice, not an oversight. (This means the existing `kampanyalar` key inside `components.py::CATEGORIES` is a same-named but *unrelated* thing — don't confuse `/kampanyalar` the new page with that backend category key.)

**The reused piece**: `UI/src/components/widgets/TableWidget.tsx` + `ProducedTable.tsx` + `TableFilters.tsx` + `UI/src/lib/contract.ts` (`TableProps` Zod schema) — this is the app's one generic table renderer, originally built for AI-produced topic-page tables. The new pages feed it real data through an adapter (`toTableProps()` in `CompareTablesBrowser.tsx`) instead of building a second table UI.

### Critical Files

| File | Purpose | Relevance |
|------|---------|-----------|
| `UI/src/components/widgets/CompareTablesBrowser.tsx` | The whole UI: subcategory dropdown → card grid → table detail. Takes a `category` prop. | **This is what needs polishing.** |
| `UI/src/app/[locale]/(app)/urunler/page.tsx` | 3-line page, `<CompareTablesBrowser category="ürün" />` | Route entry, unlikely to need changes |
| `UI/src/app/[locale]/(app)/kampanyalar/page.tsx` | Same, `category="kampanya"` | Route entry, unlikely to need changes |
| `api/routers/compare_tables.py` | `GET /api/compare-tables?category=...` (list) and `GET /api/compare-tables/{id}` (detail) | Backend; probably no changes needed for pure UI polish |
| `api/schemas/compare_tables.py` | `TableSummaryOut`, `TableListOut`, `TableDetailOut`, `ColumnOut`, `RowOut` | Backend schemas |
| `UI/src/lib/api.ts` | `api.compareTablesList(category)`, `api.compareTable(id)` (search "compare-table" in the file) | Frontend API client |
| `UI/src/lib/contract.ts` | `TableProps` Zod schema, `resolveTable()` — governs how `TableWidget` normalizes columns/rows | Read this before changing anything about how tables render |
| `UI/src/components/widgets/TableWidget.tsx` | Owns filter/sort state, renders one table via `ProducedTable` | The actual table grid — polish here benefits both this feature AND every AI-produced table elsewhere |
| `UI/src/components/widgets/Comparator.tsx` | The existing, well-polished `/compare` page — **the visual reference to match** | Look here for the app's card/spacing/typography idioms |
| `UI/src/vision/routes.js` | Sidebar nav — `urunler`/`kampanyalar` entries already added with `IoPricetags`/`IoMegaphone` icons | Only touch if changing icons/labels |
| `UI/messages/tr.json` / `en.json` | `compareTables` namespace has all current UI strings | Add new keys here for any new UI text — **restart the dev server after editing**, next-intl caches messages and HMR won't pick up new keys |
| `UI/src/types/vision.d.ts` | Module augmentation for MUI `Theme` (`borders`) — `palette.inputColors` is NOT globally augmented | See "Gotchas" below |

### Key Patterns Discovered

- **Bank slug mismatch**: `data/_tables/*.json` uses site-crawl folder slugs (`vakifkatilim`, `emlakkatilim`, `dunyakatilim`, `ziraatkatilim`, `tombank`, `adilkatilim`, `hayatfinans`, `turkiyefinans`, `kuveytturk`, `albaraka`) but the rest of the app (bank logos, `GET /api/banks`, `contract.ts`'s `KNOWN_BANKS`) uses short provider keys (`vakif`, `emlak`, `dunya`, `ziraat`, `tom`, `adil`, `hayat`, `turkiyefinans`, `kuveytturk`, `albaraka`). This is bridged **once**, in `api/routers/compare_tables.py::BANK_KEY` dict. If you add any other place that touches bank names from this data, reuse that mapping — don't re-derive it.
- **Pydantic `null` vs Zod `undefined`**: `TableDetailOut`'s optional fields (`ColumnOut.type`, `RowOut.cite_url`) serialize as JSON `null` (Python `Optional[str] = None`), but `TableProps`'s Zod schema (`contract.ts`) only accepts `undefined` for those — passing the raw API response straight into `<TableWidget {...data} />` fails `tsc`. Fixed via `toTableProps()` in `CompareTablesBrowser.tsx`, which maps `?? undefined`. If you add more optional fields to the backend schema, remember to extend this adapter too, or `tsc --noEmit` will catch it (it did, twice, this session).
- **MUI Theme augmentation is split across two files**: `borders` (used for `borderRadius`, `borderWidth`) is globally augmented in `UI/src/types/vision.d.ts`, so any `Theme` import gets it for free. `palette.inputColors` (used for border colors on hover/focus) is **not** globally augmented — every component that needs it (see `Dropdown.tsx`, and now `CompareTablesBrowser.tsx`) defines its own local `type VisionTheme = Theme & { palette: Theme["palette"] & { inputColors: {...} } }` and casts the `sx` callback's `theme` param to it. Do this again for any new hover/focus-styled element rather than assuming `theme.palette.inputColors` just works.
- **This repo's Next.js is v16.3.0**, not the version in most training data — `UI/AGENTS.md` warns about this explicitly and points at `node_modules/next/dist/docs/` for the real docs. Async `params: Promise<{ locale: string }>` + `await params` is the correct pattern (copied from `UI/src/app/[locale]/(app)/compare/page.tsx`, works, verified).
- **i18n messages need a server restart**, not just HMR, per this project's own standing convention (see repo memory `i18n-json-needs-server-restart.md` if you have access to it, or just remember it directly) — if new translation keys don't show up, restart `next dev`, don't assume you did something wrong in the JSON.
- **`Grid` from `@mui/material/Grid` uses MUI v5's `item xs={...}` API** (confirmed via `package.json`: `@mui/material ^5.18.0`), matching `CategoryComponents.tsx`'s usage exactly — not MUI v6/v7's `Grid2` API. Don't "helpfully" migrate this to `size={{...}}` syntax, it'll break.

## Work Completed

### Tasks Finished

- [x] Investigated and fixed the wrong embedding pipeline being run (`index/sync.py` reading stale `corpus_data/`, superseded per repo `.gitignore` comments) — deleted that Qdrant collection (`bank_chunks`), ran the correct one (`python -m dataprep.embed --recreate` → `campaigns` collection, 20,442 chunks) plus the table pool reindex (`compare_tables`, 309 tables)
- [x] Found and fixed a real bug: `date_pass.py`'s LLM-inferred `campaign_status` ("bitti"/"bitmedi", used when no explicit end date exists) was computed and written to frontmatter but silently dropped at embed time, never reaching Qdrant metadata or query-time filtering. Fixed in `dataprep/embed.py` (now captured), `dataprep/compare/retrieval.py::search_bank`, `dataprep/compare/pipeline.py::_fresh_enough`, and `agent_cli.py::search_corpus` (now all honor it as an expiry fallback when no date exists)
- [x] Audited the 309-table comparison pool: found 4 genuine duplicate pairs (different research passes over the same product, complementary not identical data — still unmerged, flagged not fixed), 2 fully-empty tables (legitimate "no bank offers this" findings, not failures — sources verified), and 32/65 subcategories that were single-table fragments from inconsistent LLM naming
- [x] Built `dataprep/compare/subcat_consolidate.py` — an LLM-driven (no embedding-similarity, pure content-based judgment per the user's explicit instruction) subcategory consolidation tool. Ran it: 65 → 57 subcategories, 13 tables reassigned, re-embedded into `compare_tables`. Correctly avoided a tested false-positive case (didn't merge "yatırım hesapları" with "yatırım fonu teminatlı finansman" despite name similarity, because content differs)
- [x] Hardened the *ongoing* subcategory guardrail in `dataprep/compare/synth.py` (`synthesize_table()`, `merge_tables()`) — now shown example table docstrings per existing subcategory (via new `store.py::subcategory_examples()`), not just bare names, so future table creation judges fit by content instead of by name alone
- [x] Built the full `/urunler` and `/kampanyalar` pages end-to-end: backend schemas + router, frontend types regenerated, `api.ts` client methods, `CompareTablesBrowser.tsx` widget, two page files, nav entries, i18n strings
- [x] Verified: backend endpoints tested directly with real data; both pages return 200 with correct SSR content; **full `tsc --noEmit` passes clean** (caught and fixed 2 real type errors — the null/undefined mismatch and an untyped theme callback); dev server logs show no compile/runtime errors

### Files Modified (this session, UI/backend portion only — data-pipeline files are separate and already committed per the commit list above)

| File | Changes | Rationale |
|------|---------|-----------|
| `api/schemas/compare_tables.py` | **New file.** `TableSummaryOut`, `TableListOut`, `ColumnOut`, `RowOut`, `TableDetailOut` | Wire shapes for the two new endpoints |
| `api/routers/compare_tables.py` | **New file.** `GET /compare-tables?category=`, `GET /compare-tables/{id}` | Serves `data/_tables/*.json`, converts bank slugs, shapes rows for `TableWidget` |
| `api/routers/__init__.py` | Added `compare_tables` to imports and `ROUTERS` tuple | Registers the new router under `/api` |
| `UI/openapi.json` | Regenerated via `npm run api:schema` | Picks up the new endpoints |
| `UI/src/types/api.ts` | Regenerated via `npm run api:types` (`openapi-typescript`) | TS types for `TableSummaryOut`/`TableListOut`/`TableDetailOut`/etc. |
| `UI/src/lib/api.ts` | Added `TableSummary`/`TableListOut`/`TableDetailOut` type aliases, `compareTablesList()`, `compareTable()` under a new "comparison-table pool" section | Frontend API client, follows existing file's exact pattern |
| `UI/src/components/widgets/CompareTablesBrowser.tsx` | **New file.** Full widget: category-scoped subcategory `Dropdown`, card grid picker, `toTableProps()` adapter, table detail view via `TableWidget` | The actual feature — **this is the file to polish** |
| `UI/src/app/[locale]/(app)/urunler/page.tsx` | **New file.** 3-line page | Route entry |
| `UI/src/app/[locale]/(app)/kampanyalar/page.tsx` | **New file.** 3-line page | Route entry |
| `UI/src/vision/routes.js` | Added `IoPricetags`/`IoMegaphone` imports, two nav entries ("Ürünler" → `/urunler`, "Kampanyalar" → `/kampanyalar`) between "Karşılaştır" and "Bankalar" | Sidebar nav |
| `UI/messages/tr.json` / `en.json` | Added `nav.urunler`/`nav.kampanyalar` and a new `compareTables` namespace (titles, labels, loading/error/empty strings) | i18n |

### Decisions Made

| Decision | Options Considered | Rationale |
|----------|-------------------|-----------|
| Two independent pages, not integrated into the existing `api/routers/components.py` 9-category system | (a) fit into existing system, distributing ürün tables across `sigorta`/`kartlar`/`finansman`/etc. (b) build 2 new separate pages | User explicitly chose (b) — called the existing 9-category system "hypothetical structure that will need to be cleaned" later. This was asked and confirmed mid-session, not assumed. |
| One widget (`CompareTablesBrowser`) handling browse+pick+view as internal state, not 3 separate routes/URL params | (a) single component, internal state (b) URL-driven state (`?subcategory=&table=`) | Chose (a): nothing in this flow is worth bookmarking on its own, and URL round-tripping added complexity for no user benefit. **Revisit if the next agent wants deep-linkable/shareable table URLs** — that would require URL-driven state, not exposed today. |
| Subcategory filter = single-select `Dropdown`, not `MultiSelect` | (a) `Dropdown` (pick one) (b) `MultiSelect` (app's existing tick-list-with-select-all pattern) | `Dropdown` chosen for simplicity — narrowing to one subcategory at a time matches the "browse then pick" mental model. `MultiSelect` would let multiple subcategories show at once, which might genuinely be nicer for the "polish" phase — worth reconsidering. |
| Card grid for the table picker (not a plain list or table) | (a) card grid (b) simple dropdown of table names (c) searchable list | User was asked and picked card grid, matching the Vision dashboard's card-heavy visual language — but the actual cards built this session are **plain, unstyled `VuiBox` divs with a hover border**, not real polished cards. This is the single biggest visual gap to close. |
| Reuse `TableWidget`/`ProducedTable` instead of building a bespoke table view | (a) reuse (b) build new | Reuse, no real alternative considered — this is the whole point of `contract.ts`'s design (one generic renderer for anything producer-shaped), and building a second one would violate the project's existing "don't build a parallel styling system" principle. |

## Pending Work

## Immediate Next Steps

1. **Visually verify the flow in an actual browser** — this session had no browser/screenshot tool available, so the interactive subcategory-filter → card-click → table-render path was only verified via `curl`, server logs, and `tsc`, never actually seen rendered. Start here before polishing anything, to find what's actually wrong versus theoretically fine.
2. **Polish the table-picker cards** in `CompareTablesBrowser.tsx` (the `VuiBox` block around line ~155-180 as of this session) — currently a bare bordered box with three `VuiTypography` lines (topic, docstring, subcategory). Look at how other cards in the app are styled (e.g. `Comparator.tsx`'s result cards, or dashboard stat cards) for spacing/shadow/hover conventions to match.
3. **Reconsider empty/loading states** — currently just a plain `VuiTypography` line for loading/error/empty. The rest of the app likely has nicer skeleton/spinner patterns worth reusing (check `TableWidget.tsx`'s own empty-table handling, or dashboard loading states) — search for "Skeleton" or "CircularProgress" usage elsewhere first.
4. Consider whether the subcategory filter should be `MultiSelect` instead of `Dropdown` (see "Decisions Made" above) — depends on how the polished card grid actually feels with 53 ürün subcategories vs. 14 kampanya ones.
5. Decide if table detail view needs a way to get back to a *specific* previous filter state (currently "back to list" resets nothing, `subcategory` state persists since it's not cleared on `setTableId(null)` — verify this is actually the desired behavior, not an oversight).

### Blockers/Open Questions

- [ ] No browser tool was available this session — all UI verification was indirect (curl, tsc, server logs). The next agent should confirm real rendering before trusting anything visual described here.
- [ ] The 4 genuine duplicate table pairs and low-coverage tables flagged during the data audit (see "Tasks Finished") are **not fixed** — out of scope for this UI-focused handoff, but the next agent should know duplicate/sparse tables can currently appear in the card grid picker. Not a UI bug, a data quality gap upstream.
- [ ] Whether deep-linkable table URLs (`/urunler?table=xyz`) are wanted — not built, flagged as a "Decisions Made" reconsideration point.

### Deferred Items

- Merging/fixing the 4 duplicate table pairs (`alışveriş-finansmanı`/`-2`, `zorunlu-deprem-sigortası`/`-2`, `zorunlu-trafik-sigortası`/`-2`, `ithalat-finansmanı`/`-2`) — deferred because it's a data-layer task (would use `dataprep/compare/dedup.py`'s `synth.merge_tables()`), not a UI task. Don't let it block UI polish, but don't let a duplicate-looking card confuse you into thinking the UI has a bug when it's actually the underlying data.
- Distributing/mapping into the other 9-category `components.py` system — explicitly deferred/rejected for now per the "Decisions Made" table above.

## Context for Resuming Agent

## Important Context

- **The backend is done and tested; this handoff is scoped to frontend visual polish only.** Don't re-architect `api/routers/compare_tables.py` unless polish work surfaces a genuine data-shape problem (e.g., a column type that should render as `money`/`percent` instead of plain text — check `ColumnOut.type` handling and `contract.ts::inferColumnType()` if numeric-looking columns render wrong).
- **`category` is a hard 2-value enum** (`"ürün"` | `"kampanya"`) enforced upstream by the data pipeline's own LLM prompt — safe to treat as permanently fixed, no need to fetch it dynamically or handle a hypothetical third value.
- **`subcategory` counts will keep drifting slightly** as the data pipeline re-runs (57 today, was 65 before this session's consolidation) — don't hardcode subcategory lists anywhere in the UI; always fetch from `GET /api/compare-tables?category=...`, which the current code already does correctly.
- The two pages are **not yet in git** — check `git status` and decide with the user whether/when to commit, this handoff doesn't assume that decision was made.

### Assumptions Made

- Assumed the user wants visual polish only, not new functionality (filters, search, sorting beyond what `TableWidget` already provides) — re-confirm with the user if the "make it better" ask turns out to mean something more structural.
- Assumed bank display names/logos elsewhere in the app (via `GET /api/banks`) will render correctly for the `"Banka"` column since `TableWidget` already fetches and caches bank display names — **not independently re-verified in a browser**, listed as immediate next step #1.
- Assumed `docstring` (shown as card subtitle) is always short enough to not need truncation — some are 1-2 sentences, should be fine, but wasn't checked against every one of the 309 tables for outliers.

### Potential Gotchas

- Editing `UI/messages/tr.json`/`en.json` and not seeing new keys appear → restart the dev server, don't debug the JSON first (see "Key Patterns Discovered").
- Adding any new theme-dependent hover/focus style → remember the `palette.inputColors` local-type-cast pattern, or you'll get an implicit-`any`/property-doesn't-exist error from `tsc` (caught twice this session already).
- If you add optional fields to `TableDetailOut`/`ColumnOut`/`RowOut` on the backend, remember `toTableProps()` in `CompareTablesBrowser.tsx` needs the same field added with `?? undefined`, or a real `tsc` error will surface (it's not just theoretical — this exact mistake happened this session and `tsc --noEmit` caught it after the dev server's own compiler missed it).
- Don't confuse `/kampanyalar` (this session's new page) with the same-named `"kampanyalar"` key inside `api/routers/components.py::CATEGORIES` — same word, unrelated systems, explained under "Architecture Overview" above.

## Environment State

### Tools/Services Used

- FastAPI backend: `uvicorn api.main:app` — was already running with `--reload` on port 8002 during this session (started before this session, not by it)
- Next.js dev server: `next dev` — was already running on port 3000 (also pre-existing, not started by this session)
- Qdrant (vector DB) — running locally at `http://localhost:6333`, used by the data-pipeline side of this session, not directly by the UI work
- `npx tsc --noEmit` — used to verify the frontend, run from `UI/`

### Active Processes

- Both dev servers above were left running at the end of this session. Verify they're still up before assuming `curl localhost:3000` / `localhost:8002` will work; if not, restart per each service's own run instructions (`uvicorn api.main:app --reload`, `npm run dev` from `UI/`).

### Environment Variables

- None specific to this feature — no new env vars were introduced. The existing `VLLM_BASE_URL`/`QDRANT_URL`/etc. (used by the data-pipeline side of this session) are unrelated to the UI work in this handoff.

## Related Resources

- `UI/src/components/widgets/Comparator.tsx` — the visual reference to match for polish (well-established card/spacing/typography patterns)
- `UI/src/lib/contract.ts` — read fully before changing anything about how columns/rows render; it documents its own design philosophy in comments
- `dataprep/compare/README` (if one exists) or `dataprep/compare/pipeline.py`'s module docstring — background on where the table data itself comes from, useful if a data-shape question comes up during polish

---

**Security Reminder**: No secrets, API keys, or credentials appear anywhere in this document.
