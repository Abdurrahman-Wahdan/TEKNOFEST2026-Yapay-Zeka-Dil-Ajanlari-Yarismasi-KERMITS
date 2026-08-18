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

Components with no importer live in `src/components/_unmounted/` (see the README
there). If you need one, move it back — do not write a new lookalike.

# The drawer

Collapses to a 96px icon rail rather than closing — at every width. Expanded it
is the `KERMİTS` wordmark on the left and the collapse button on the right;
collapsed it is the logo mark alone, centred, which cross-fades to the expand
button while the pointer is anywhere over the drawer (`hovered` in
`examples/Sidenav/index.js`). Hover reveals the button; it never expands the
drawer.

The state is the **user's**. It persists in the `tf26.sidenav` cookie, seeded
server-side in `src/app/[locale]/(app)/layout.tsx` so a collapsed drawer renders
collapsed in the first HTML. Do not reintroduce anything that derives it from
`window.innerWidth` — an effect doing that on resize *and* route change is what
used to wipe the choice on every nav click.

Widths live in `src/vision/sidenavWidths.js` and nowhere else.
