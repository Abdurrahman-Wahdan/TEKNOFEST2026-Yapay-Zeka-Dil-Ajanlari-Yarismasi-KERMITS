@AGENTS.md

# Unmounted pages

Several pages are **unmounted**, not deleted: their page files sit verbatim in
`src/app/[locale]/(app)/_unmounted/` and their drawer entries are commented out
in `src/vision/routes.js`. Which pages are unmounted changes often — `ls` that
folder rather than trusting a list. Remounting is a `git mv` back plus
uncommenting the entry and its icon import; unmounting is the reverse. Every component they
render is still live — `src/vision/layouts/{dashboard,tables,billing}` and
`src/components/widgets/BankRegistry` — so reuse them, or take the pattern, when
building new pages. Do not delete any of it.

`/compare` is the landing page: both `/` and post-login redirect there. It is
the first entry left in the drawer — if that changes, change both redirects.

To remount one, see `src/app/[locale]/(app)/_unmounted/README.md`.

# One component per job

There is **one** table in this app: `src/components/widgets/ProducedTable.tsx`.
Every table page renders it — `/compare` via `Comparator`, `/urunler` and
`/kampanyalar` via `CompareTablesBrowser`, and `/finansman` via `TableWidget`
when it is remounted. Shared
table behaviour belongs *inside* it, not in an sx wrapper at one call site:
row hover used to be bolted on by `TableWidget` alone, which is exactly why only
one of the four pages had it. Same rule for the shared logic around it —
`useTableSort` and `useBankLabels` in `src/lib/` are the single copies of the
three-click sort toggle and the bank display-name map.

`src/components/ui/Pill.tsx` is the only status chip: always outlined, never
filled, drawn the way the Vision template's own status badges are. The tone
lives in the border; the text is ink in every variant.

Before writing a new table, card, list, sort control, pill or empty state, look
for the existing one and extend it. Variation by prop is fine; a second
component doing the same job is not.

The one deliberate exception is `src/components/chat/MarkdownTable.tsx`, which
draws the tables in the assistant's markdown. It is not a lookalike:
`ProducedTable` takes typed `ResolvedColumn[]` and a sort handler, and a markdown
table has arbitrary string headers, string cells and no types at all — routing it
through `ProducedTable` would mean inventing column types and passing a no-op
sort. What the rule protects is the *look*, and that is shared:
`src/lib/table-style.ts` holds the column gutter, the rule colour and the row
hover, and both renderers read from it. Neither is allowed a table style of its
own.

Components with no importer live in `src/components/_unmounted/` (see the README
there). If you need one, move it back — do not write a new lookalike.

# The drawer

On a tablet and up it collapses to a 96px icon rail rather than closing.
Expanded it is the `KERMİTS` wordmark on the left and the collapse button on the
right; collapsed it is the logo mark alone, centred, which cross-fades to the
expand button while the pointer is anywhere over the drawer (`hovered` in
`examples/Sidenav/index.js`). Hover reveals the button; it never expands the
drawer.

**Below `md` it is a different drawer.** MUI's `temporary` variant, full width,
closed on arrival, opened by the menu button in `DashboardNavbar` and dismissed by
the backdrop or by picking a destination — checked against ChatGPT, which does the
same. A 96px rail on a 375px screen spends a quarter of the width on navigation
and squeezes every page into what is left, which is what made the chat composer
unusable there. `DashboardLayout` drops its left margin to 0 at the same
breakpoint, since there is no docked drawer to make room for.

That switch reads the viewport, and it is *not* the thing the next paragraph
forbids: it changes the drawer's `variant`, never `miniSidenav`. Phone visibility
is its own state (`mobileNavOpen`, not persisted — an overlay that reopened itself
on the next page would cover the page). The rail-or-expanded choice stays the
user's and survives a visit on their phone untouched.

The state is the **user's**. It persists in the `tf26.sidenav` cookie, seeded
server-side in `src/app/[locale]/(app)/layout.tsx` so a collapsed drawer renders
collapsed in the first HTML. Do not reintroduce anything that derives it from
`window.innerWidth` — an effect doing that on resize *and* route change is what
used to wipe the choice on every nav click.

Widths live in `src/vision/sidenavWidths.js` and nowhere else.

# The assistant

Two surfaces, one implementation. `src/components/chat/ChatPanel.tsx` is the
conversation — transcript plus composer — and both surfaces render it:
`AgentPopup` (the floating panel, mounted in `VisionApp` where the theme FAB used
to be) renders it as-is, and `/chat` passes `emptyState="center"`. Its props are
`{ emptyState, autoFocus, placeholder }` — there is one composer, not two
variants. Do not fork it.

The conversation lives in `src/lib/chat/ChatProvider.tsx`, mounted in
`src/app/[locale]/(app)/layout.tsx` **above** `VisionApp`. That placement is the
whole design: the popup and the page sit in different trees, so hoisting the state
above both is what makes the popup's expand button plain navigation instead of a
serialise-and-rehydrate step, and what keeps a conversation alive across a click
from /compare to /urunler.

Losing the theme FAB cost nothing — `DashboardNavbar` has always carried a second
theme toggle, and `ThemeToggleFab` is still exported from
`components/VuiThemeToggle` if it ever needs to come back.

`ChatComposer` is ChatGPT's composer, checked against the real thing: **one row
while the text fits on one line — attach, field, Think, mic, send — and the
controls drop to a row underneath the moment it wraps.** The radius is a constant
28px, so the short state reads as a pill and the tall state as a rounded box with
nothing animating between shapes. The field grows to 300px and then scrolls, so a
pasted wall of text cannot make the composer taller than the window.

Both states are the **same DOM**; only the controls' position changes, absolute
inside the field row to static below it. Moving the field between two parents
instead would remount it, and typing past the wrap point would lose focus and the
caret mid-sentence.

Toggles are borderless and transparent until active, then a `color-mix` tint of
`--primary` with matching text. Do not reach for `--info-subtle` here: in the dark
palette it is `--accent` (#061622, all but black), which is how an active toggle
came to look identical to an inactive one.

The matching text is `--primary-strong`, never `--primary`: the palette's primary
is a *fill* colour and measures 2.76:1 as ink on the light `--card`, so an active
Think chip was light blue on pale blue. Quiet control text -- the placeholder, an
inactive toggle's label, an icon button at rest -- is `--control-ink`, not
`--muted-foreground` (3.88:1 on the dark card) and not `--text-faint` (2.49:1,
which is for decoration only). Both tokens are derived in
`src/styles/tailwind.css` and clear 4.5:1 in both themes.

`@` opens a menu of the staged attachments (`MentionMenu`, `mentionAt`) so a
question about "the statement" can name which file it means. It only appears once
something is attached. Files stage locally in `useAttachments` — object URLs for
image thumbnails, revoked on removal — and travel on the request as metadata,
because there is still no upload endpoint.

**The backend is not connected.** `src/lib/chat/transport.ts` is the only seam:
`streamChat` points at a mock that streams canned markdown so partial-markdown
rendering stays verifiable. Point it at `fetchChat` — already written beside it —
when the agent lands. `IS_MOCK_TRANSPORT` drives the "not connected yet" notice,
so that disappears on its own. The composer's Think and Deep Search toggles set
real flags on the request that the backend does not read yet.

Markdown is rendered by **Streamdown**, not react-markdown, because the agent
streams and a normal renderer handed half a table renders it broken. It needs the
two `@source` directives in `src/styles/tailwind.css` to emit its utilities at
all, and `src/components/chat/AgentMarkdown.tsx` restyles its code-block chrome
through the `data-streamdown` hooks. It is dynamically imported (`ssr: false`) —
Shiki is large and the popup mounts on every page.

# Bringing the page into the conversation

The assistant can see what the user is looking at. Three ways in, one concept
underneath: a **staged context attachment**, alongside the files `useAttachments`
already holds. The tray, the `@` menu, the per-turn clearing and the wire format
are written once.

**Select anything → "Ask about this."** `SelectionReply` is mounted once in
`VisionApp` beside `AgentPopup` — one listener for the whole dashboard. The quote
becomes an *attachment*, never text pushed into the composer: the composer's value
is local state in a component that exists twice and may not be mounted, and the
user's own words are deliberately not rendered as markdown, so a `> quote` would
show a literal `>`. Suppressed inside `[data-no-quote]` (the composer) and in any
field; allowed in the transcript, because quoting the agent's own answer is the
most valuable case. Snapshot the selection's rect, never hold the live `Range` —
MUI crashes on `getRangeAt(0)` when `rangeCount` has dropped to zero.

**Attach a row, or a whole table.** Both are optional props on `ProducedTable`
(`onAttachRow`, `onAttachTable`), so every table page gets them and no call site
can forget — the same reasoning as row hover. The trailing action column renders
only when a handler is passed, and it needs a filler entry in `groups` (spans must
sum to `columns.length`) plus `data-no-outline` so the page snapshot skips it.
`useAttachTable` is the single hook all three call sites use; give it the
**filtered, sorted, visible** rows, because attaching a 200-row table the user has
narrowed to three is attaching something they never saw.

**Attach a whole report.** `/profile/reports` puts the same control in the open
report's header (`ReportsBrowser`, `kind: "report"`), staging `report.body`
verbatim — a report *is* an assistant answer in markdown, so there is nothing to
reserialise and nothing to read back out of the rendered DOM. Its `location`
carries the page and the report's date rather than its title, because the chip is
already labelled with the title and a subline repeating it says nothing twice.

The button itself is `src/components/chat/AttachButton.tsx` — one copy, shared by
the table and the report. It started inside `ProducedTable`; a second lookalike is
how one of them ends up a different size or icon from its neighbour. `alwaysVisible`
is required outside a table row: the hover reveal is `tr:hover`, so a button that
lives anywhere else is one nobody can see.

**Formats are not a matter of taste.** Benchmarked across eleven encodings,
markdown key/value leads (~61% answer accuracy) and a markdown table reaches ~52%,
while **CSV is last at ~44%**. So: one record as key/value, many as a GFM table.

**Nothing sent to the model is truncated** — not a table, not a quoted row, not a
page snapshot. There were caps (25 rows in a snapshot, 50 in an attached table) and
they were a mistake: a 30-row board arriving as 25 rows cannot answer "which bank
is cheapest", so the agent asks a follow-up, which is the whole thing this feature
exists to remove. Half a table does not save tokens, it wastes a turn. The budget
supports it — Gemma 4 carries 128k/256k and the producer contract caps a table at
`MAX_ROWS` (500) and a page at `MAX_COMPONENTS` (8). If a payload ever does exceed
the window, the request fails visibly instead of the agent quietly answering from
part of the data. Do not reintroduce a cap here.

Everything lives in `src/lib/chat/context-format.ts`, and every cell goes through
`src/lib/cell-display.ts`, which is also what `ProducedTable`'s own `Cell` reads:
when those drifted, the agent answered about `kuveytturk` while the user was
looking at *Kuveyt Türk*.

**Provenance is most of the value.** `src/lib/chat/page-locator.ts` works out
where something came from — page, section, table, row, column — and a quoted cell
travels with its whole row, marked `←` at the selected cell. Coordinates go out as
XML attributes because an agent parses those better than prose. Tables declare
themselves via `title`/`about` props → `data-table-title`/`data-table-about`,
passed as **data from the call site**, never inferred from nearby headings: the
caller knows the title exactly, and guessing from markup breaks the moment a page
changes layout. On `/compare`, `about` is built from the *submitted* query, not the
live form — the fields can be edited after Compare was pressed, and the table on
screen still belongs to the old query.

**"Look at my screen" is a client tool.** The agent asks, the browser answers, and
the request is re-issued with the result — `MAX_TOOL_PASSES` caps the loop.
`src/lib/chat/tools.ts` is the closed registry; an unknown name is refused, not
dispatched. Two tools, and the agent should almost always pick the first:

- `read_page` → `page-outline.ts`, a semantic outline. Exact figures, a fraction of
  the tokens of real DOM (this app is MUI + emotion + the Vision template, so the
  markup is mostly wrapper divs), and unit-testable. **Current selections come
  first** — which product, what amount, what term — because that is the state that
  explains every figure on the page.
- `capture_page` → `capture.ts`, a WebP of `[data-page-root]`. For questions about
  the rendering, not the data.

**snapdom, not html2canvas.** html2canvas reimplements the renderer in JS and
throws on `oklch()`/`color-mix()`; every derived token in `tailwind.css` is a
`color-mix()`, so it would fail on our own chrome. snapdom serialises and lets the
*browser* rasterise. Pin `dpr: 1` — snapdom multiplies `scale` by the device pixel
ratio, and without it a 1280 cap produced 2134px images. Scale from the width of
the **element being captured**, and keep no lower bound on scale: a floor of 0.5
silently broke the cap on wide layouts.

**Images must reach the model as images.** `captures` and `toolResults[].image`
carry `{mediaType, data}` — base64, no `data:` prefix — split at the client so the
backend can build a vision content block directly. The target is **Gemma 4**
(Apache-2.0, 128k/256k context, image + text, and screen/UI understanding is a
stated capability); its chat template takes `{"type": "image", "image": …}` and
wants **images before the text in the turn**, which is how the user message is
already built. Forwarding base64 as *text* shows the model a wall of characters,
answers confidently from nothing, and bills for all of it.

**The backend still reads none of it.** `streamChat` points at the mock, which
echoes every payload back — including the serialised context and the page snapshot
— so all of this is verifiable now. That echo goes away with the mock.

# The AI overview card

One card, two sources, and three cadences. `OverviewCard` draws it; `TableOverview`
feeds it from the offline pool and `LiveOverview` feeds it from `/compare`. The
agent is the same one either way (`agents/table_overview`), and what it reads is
the same thing the assistant's `look_at_page` reads — `readPageText()`, the
semantic outline. It computes nothing; it quotes the page back.

**The pool is keyed on a table id, the live page is keyed on itself.** That is the
whole difference. `data/_tables/` is offline, so an overview there is a function of
a file and lives in a database row with no expiry (`api/table_overviews.py`).
Nothing on `/compare` is like that — the FX board is a different board every few
minutes by design and a finance run belongs to one user's amount, term and bank
selection — so `api/live_overviews.py` hashes the outline and keeps a bounded
in-memory cache instead. A row per FX tick is landfill with no second reader.

**The client never hashes anything.** `POST /api/compare/overview` takes the page
and answers with either the finished overview or the digest to poll. The pool's
card can GET-then-POST because the id is in the URL it navigated to; here, having
the browser reproduce Python's SHA-256 forever is a thing that fails silently and
looks like an overview that never arrives.

**The three cadences live in `Comparator`, as `overviewRevision`.** `LiveOverview`
regenerates when that string changes and at no other time. The FX board rewrites
itself on a five-minute tick because it is the one thing that moves on its own;
miles is read once, keyed on the bank, because it is one bank's published table;
everything with a Compare button is keyed on the submitted parameters *and* on when
the result landed — which is what makes the card spin from the press and generate
from the arrival. A tick over a page that has not moved is free: the server keys on
the outline, so it serves the cache.

**Data first, then the overview.** `overviewReady` holds the generation back until
the rows are actually in the DOM. Measured in a browser: Compare pressed at 0.00s,
`/compare/finance` answers at 0.76s, the overview POST goes at 0.82s. Reading
earlier hands the model a spinner and asks it what the comparison shows.

**The card is `data-no-outline`.** It is written *from* the outline, so leaving it
visible to one would feed the model its own previous answer — a summary of a
summary, drifting further from the table every refresh. It costs nothing on the
pool's card, which is written once; it is load-bearing on the live one.

**The "have we asked yet?" guard is a ref, not `mutation.variables`.** Render state
is stale when two renders queue in one tick, and both fire — measured, two identical
POSTs 0ms apart on a second Compare. A ref is written synchronously.

# The AI dashboard (`/ai-overview`)

Per-user tables the assistant composed, one `SavedView` row each. **Almost all of
the storage for this already existed and had never been called**: the model, its
migration, `GET/PUT/DELETE /me/views`, and `api.views/saveView/deleteView`. Nothing
new was added to the schema, and nothing should need to be.

**Two ways in, one shape.**

- The agent's own **`save_table`** tool (`api/agent.py`), which writes the row
  server-side. Offered only when `answer()` was given a `user_id`.
- **"Keep this table"** on any markdown table in a chat answer, hovering into view
  above it (`components/chat/MarkdownTable.tsx`).

Both land as `components: [{type: "table", props}]` and render through
`RenderComponent` → `TableWidget` → `ProducedTable` — the same path a produced
topic-page table takes. **There is no second table renderer and there must not be
one.** Both also set `generated: true`.

**Only tables the user asked for.** Nothing saves itself: the model decides, and an
available tool is an attractive tool, so the rule is written in `SYSTEM_PROMPT`
*and* repeated in the tool's own description (the string read at the decision
point). A comparison the agent volunteers is *not* a request — that one gets the
button instead, so it is the user's click rather than the model's judgment.

**The tool takes a flat matrix** — `columns: string[]`, `rows: string[][]` — not
`{cells: {...}}`. A nested object is the likeliest thing for the model to get
wrong, tool arguments arrive split across stream chunks, and a header-plus-matrix
is exactly what a markdown table already is, so both paths produce identical props.
`api/saved_tables.py::table_props` does the conversion.

**Two slugifiers, one behaviour.** `slugify` in `api/saved_tables.py` and
`slugifyTitle` in `src/lib/saved-view.ts` must agree, or the same title saves twice
under two slugs. Both **transliterate Turkish before lowercasing**, because `"İ"`
lowercases to `i`+U+0307 in Python and `i̇` in JavaScript — that is exactly where
they would diverge. The shared case table lives in both test files; change both.

**A collision overwrites.** `PUT /me/views/{slug}` is already an upsert, so
suffixing would make the tool behave differently from the HTTP route on the same
storage — and it makes a repeated question a refresh rather than `konut-2`,
`konut-3`. The cost is real: two different tables with the same title clobber each
other, addressed by asking for a distinguishing title in the tool description.

**Column types are left off on purpose** so `inferColumnType` reads the values.
Coercing "%2,89" or "28.410 TL" into numbers destroys "↓ 0,26" for no gain. The one
exception is the `kaynak` source column, which must stay `link`.

**No truncation.** No row cap, no cell cap, pinned by tests on both sides. The only
clips are the `slug` (80) and `title` (160) column widths, and the title clip is
logged because it shortens a string a person reads.

## The tool loop in `answer()`

Two kinds of tool now, and they behave differently. `look_at_page` runs in the
**browser**, so its call ends the stream and the client asks again.  `save_table`
runs **here**, so the write happens in-process, the result goes back as a
`ToolMessage`, and the answer continues in the same response. Server calls run
*first*, before any chance of the stream ending, so a write is not lost if the
client never returns with the page.

**There is deliberately no pass limit.** A count breaks real work — "make me five
tables" is five passes, and a cap of three stops at the third with the model
believing it saved five. Termination is by **progress**: a call whose
`fingerprint(name, args)` already ran is not run again, and a turn producing no
fresh call ends. That bounds the pathology and leaves the legitimate case alone.
(`ChatProvider.tsx` still carries `MAX_TOOL_PASSES` on the *client* loop; same
objection applies and it should get the same treatment.)

**Every tool failure must be prose, never an `error` frame.** `api/routers/chat.py`
sets `failed` on an error and then discards the whole assembled answer — so a
failed save would delete a good answer. `save_table_view` never raises. Only a
*client* tool sets `awaiting_tool`, which is why that check names `CLIENT_TOOLS`.

A `saved_view` StreamEvent carries `view_slug`/`view_title` so the UI can say so
and link without re-fetching. Regenerate `src/types/api.ts` after touching it.

**Verified without the model.** `save_table_view` takes its session factory as an
argument, so the write path is unit-tested with no database; `_FakeLLM` serves one
scripted chunk list per pass. The mock transport's canned answers already contain
GFM tables, so chat → save → `/ai-overview` is a complete loop today. What is *not*
verified is Gemma 4 actually choosing the tool, filling the matrix, accepting an
assistant-with-`tool_calls` plus `ToolMessage` on the follow-up, and obeying the
save-only-when-asked rule — the vLLM host 404s.

**`PageHeader` sets its title colour explicitly.** Inside `AppPage` the Vision
dashboard container sets a near-white text colour for its own dark template, so an
inherited heading came out invisible on the light theme. This page is the first to
put `PageHeader` inside `AppPage`, which is why nothing had caught it.
