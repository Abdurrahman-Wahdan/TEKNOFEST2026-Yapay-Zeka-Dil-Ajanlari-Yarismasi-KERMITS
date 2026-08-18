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
to be) passes `composer="compact"`, and `/chat` passes `composer="hero"` with
`emptyState="center"`. Do not fork it.

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
