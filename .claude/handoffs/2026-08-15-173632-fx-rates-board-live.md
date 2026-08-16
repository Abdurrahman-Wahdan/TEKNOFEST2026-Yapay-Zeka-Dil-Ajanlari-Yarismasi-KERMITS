# Handoff: TF26 live comparison — bank endpoint discovery and the FX Rates board

## Session Metadata
- Created: 2026-08-15 17:36:32
- Project: /Users/abdelrahmanwahdan/Desktop/TF26
- Branch: abdelrahman
- Session duration: one long session, uncommitted throughout

### Recent Commits (for context)
  - 998b16c Merge remote-tracking branch 'origin/main' into abdelrahman
  - 543a454 compare: banka-arası kampanya/ürün karşılaştırma pipeline'ı + tarih damgalama
  - ced6487 Merge pull request #10 from Abdurrahman-Wahdan/furkan
  - 805b597 compare: banka-arası kampanya/ürün karşılaştırma pipeline'ı + tarih damgalama
  - 5487ce2 feat: add table filtering and sorting functionality

## Handoff Chain

- **Continues from**: None (fresh start)
- **Supersedes**: None

## Current State Summary

Everything is **uncommitted** on `abdelrahman` — 112 changed files. All suites pass
(backend 592 unit + 91 integration, frontend 109, live health 37/37), typecheck and
lint are clean, i18n resolves in both locales.

The session did three things. It rebuilt the **product family map** for financing and
participation accounts after finding it was wrong in ways that produced confident false
answers. It then **hunted the banks' own endpoints** and found four banks publishing
rates we were not reading at all. Finally it built the **FX Rates board**: grouped
per-bank headers, three filters, live updates over a WebSocket, and movement colouring.

Work stopped mid-way through visual polish on that board. The last three requests were
all "this doesn't look right" fixes made **without being able to see the page** — the
browser MCP disconnected early and never came back.

## Architecture Overview

The hard split, decided by the user and load-bearing everywhere:

> **Live bank endpoints → deterministic software, always.
> RAG corpus → AI-produced components, always. The two never cross.**

`banks/` talks to ten banks. `api/` converts dataclasses to response models — `raw` is
dropped at that boundary on purpose. `UI/` is Next.js 16 + the Vision UI template.

The RAG half (`corpus/`, `index/`) is **the teammate's**, and he will push to main. Do
not work there. This session touched it only to fix a production outage (below).

## Critical Files

| File | Purpose | Relevance |
|------|---------|-----------|
| `banks/families.py` | family → banks → each bank's own code | The map. `Member` carries `variant` and `general`. |
| `banks/taxonomy.py` | decomposes a product name into axes | How the map is kept honest; a test fails when it falls behind. |
| `banks/parse.py` | `canonical_currency`, `money`, `money_en` | Currency unification. Dünya needs `money_en`. |
| `api/rates_stream.py` | one poller, many viewers | The live board's backend. |
| `api/cache.py` | TTL + single-flight | Stops per-tab polling reaching the banks. |
| `UI/src/lib/comparator.ts` | builds every comparison table | `ratesBoard`, `movements`, `defaultSort`, `bestRates`. |
| `UI/src/components/widgets/Comparator.tsx` | the whole page | ~800 lines; filters, stream, table wiring. |
| `UI/src/lib/use-rates-stream.ts` | WebSocket client + fallback | Falls back to polling if the socket dies. |
| `tests/unit/test_families_coverage.py` | the map's guard rails | Fails when a product two banks sell has no family. |
| `docs/COMPARE_CAPABILITIES.md` | the measured endpoint inventory | Updated with everything found this session. |

## Key Patterns Discovered

**Find endpoints by reading the bank's own JavaScript.** Guessing route names fails;
fetching the page's `<script src>` bundles and grepping for `/plugins/`, `/api/` etc.
found every endpoint this session. It is how Vakıf, Dünya and Türkiye Finans were found.

**Never compute a figure the bank states.** Deriving a sell price by inverting a
converter matched published rates to 0.00% and was still removed — the bank publishes
it, so we read it.

**A refusal must name the real reason.** "Not offered" when the bank offers it at 0%
sent the user hunting a missing product that was there.

**next-intl caches messages.** Adding a key to `UI/messages/*.json` needs a **dev server
restart** — HMR and a page reload will not do it. This cost time three separate times.

## Tasks Finished

- [x] Rebuilt the finance family map: 10 → 15 families, 7 → 8 banks
- [x] Found the konut axis bug: `İLK EVİM` (first home) was mapped to `konut-yeni` (new build)
- [x] Türkiye Finans made comparable — publishes a rate, never an instalment (`installment=None`)
- [x] Added `katilma-altin`; found gold is a distinct product (KT pays 40% vs 95% ordinary)
- [x] Fixed `_check_profit_share` refusing correct gold quotes (relative tolerance on 0,01 gram figures)
- [x] Found four banks publishing rates we never read: albaraka 4→23, turkiyefinans 0→20, vakif 0→16, dunya 0→14
- [x] Removed all derived rates
- [x] Fixed `corpus/pdf_extract.py` — duplicated function body, `UnboundLocalError` on every call
- [x] Deleted 21 byte-identical `" 2.py"` duplicate files
- [x] Whole test suite green for the first time; lint 0/0
- [x] Built the FX board: grouped headers, 3 filters, WebSocket, movement colouring, best-price marking

## Files Modified

112 files. The ones that matter:

| File | Changes | Rationale |
|------|---------|-----------|
| `banks/families.py` | rewritten around `Member(bank, query, variant, general)` | A bank can appear twice in a family; two konut axes are different questions |
| `banks/taxonomy.py` | new | Finds products no family covers, per category |
| `banks/providers/{albaraka,vakif,dunya,turkiyefinans}.py` | real `rates()` | Four banks were publishing boards we ignored |
| `banks/providers/base.py` | `rates_from_converter`, `_agrees` | Rounding-aware profit check; the converter helper is now unused |
| `banks/models.py` | `installment/total` nullable; `variant`, `general`, `derived` | A rate-only quote is a real answer |
| `api/cache.py`, `api/rates_stream.py` | new | Safe polling, then push |
| `UI/src/lib/comparator.ts` | `ratesBoard` rewritten | Groups, pairs, units, movements, best |
| `UI/src/components/ui/Pill.tsx` | line-height centring | Third attempt — see gotchas |

## Decisions Made

| Decision | Options Considered | Rationale |
|----------|-------------------|-----------|
| Four konut families | one family; strict membership | Property condition and buyer ownership are different questions; general products join all |
| TF in scope with `installment=None` | leave out; always unavailable | It publishes a real rate for 18 products |
| Read pages where no endpoint exists | derive from converter | User: if the bank states it, we read it |
| Server polls, socket pushes | browser polls banks | Two boards are page reads; Albaraka fingerprints TLS |
| `katilma-aradonem` recorded, not shipped | ship it | Both banks sell it, neither prices it — a family that can only refuse |

## Immediate Next Steps

1. **Look at the FX board and confirm the last three fixes.** The pill's vertical
   centring (third attempt), the sort indicator showing only when active, and the
   left-aligned bank names. All were made blind.
2. **Commit.** 112 files uncommitted on `abdelrahman`. Consider splitting: family map /
   endpoint discovery / FX board / the corpus fix.
3. **Tell the teammate about `corpus/pdf_extract.py`.** Its body was duplicated, so
   `extract()` crashed on every call and **4.800 queued PDFs were never processed**. If
   his rewrite branched from that file it may carry the same fault.

## Blockers/Open Questions

- [ ] Browser MCP disconnected — no visual verification possible for the whole second half
- [ ] Pill centring may still be wrong; next suspect is the Vision `caption` variant's own line-height
- [ ] `bestRates` marks the best buy/sell in bold — never seen rendered
- [ ] Long bank names ("TÜRKİYE FİNANS KATILIM BANKASI") over two narrow columns may read badly

## Deferred Items

- "Last updated HH:MM:SS" stamp on the board — offered, not built. Would resolve
  "is it even updating?" on a weekend when nothing moves.
- Vakıf `HomepageProfitShareTable` and Hayat `lastprofitsharerates` — both return
  published profit-share rates by term. Found, verified, surfaced nowhere.
- Kuveyt Türk duplicate codes `ELKTRARACSARJUNITE` and card `BP` — the bank's own data;
  both products reachable by name, pinned by a test so a new collision fails the build.

## Important Context

**The user's standing rules, learned the hard way this session:**

1. **Never dismiss a failure.** "18 pre-existing test failures" were reported ~10 times
   before being opened; they were a production outage in the PDF pipeline.
2. **Never compute what a bank publishes.** Endpoints are non-negotiable.
3. **Do not remove things unasked.** Price-column sorting was removed on judgement and
   had to be restored.
4. **Verify, do not assert.** Every claim about a bank should be backed by a live call.

**Today is Saturday 2026-08-15.** The banks do not quote at weekends. Six samples over a
minute showed **zero price changes at any of six banks**. The board is correct and static;
the movement colouring cannot be seen until a weekday. This was verified, not assumed —
the stream pushes `v226 → v227` in 2,92s with 104 prices and 0 differing, and nudging one
value by hand made the diff flag it.

## Assumptions Made

- The three filters (Pair, Bank, Side) are enough for the board; the general
  `TableFilters` bar stays for AI tables
- Left-aligning price columns is worth losing decimal alignment (the user asked)
- `CAG (gr)` and `ZCeyrek` stay unmapped — coin gold is not bullion gold

## Potential Gotchas

- **`money()` is Turkish-format.** Dünya publishes en-US numbers on a Turkish page;
  `money()` reads `47.7023` as **477023.0**. Use `money_en` there.
- **Restart the web server after touching `messages/*.json`.**
- **Restart the API server after touching a provider** — catalogues cache for 15 minutes.
- **React Compiler rejects** reading refs during render and `setState` in effects. The
  board's movement diff uses a guarded render-phase update; a `setState` during render
  makes React discard that render, so anything computed beside it is lost.
- **Group header spans must sum to the column count** or every heading shifts one cell
  left and silently mislabels the board. There is a test.
- `banks/providers/base.py::rates_from_converter` is now **unused** — kept as the
  documented fallback for a bank that converts but publishes nothing. Safe to delete.

## Tools/Services Used

- API: `uvicorn api.main:app --host 127.0.0.1 --port 8001`
- Web: `cd UI && API_ORIGIN=http://127.0.0.1:8001 npm run dev` (port 3000)
- `.claude/launch.json` has both as `api` and `web` configs

## Active Processes

- **None.** Both background servers were stopped at session end. Restart both before
  resuming; the socket needs the API running or the board falls back to polling.

## Environment Variables

- `API_ORIGIN` — must be set for the web server or it proxies to port 8000 and 404s

## Related Resources

- `docs/COMPARE_CAPABILITIES.md` — the measured inventory, updated this session
- `docs/discovery/captured/ziraat.md` — corrected: kâr payı is reachable, returns zeros
- `docs/discovery/captured/hayat.md` — corrected: the loan endpoint needs a `request` wrapper
- `scripts/refresh_catalogues.py` — refreshes the coverage fixture
- `tests/fixtures/banks/catalogues.json` — every product name and code, captured live
