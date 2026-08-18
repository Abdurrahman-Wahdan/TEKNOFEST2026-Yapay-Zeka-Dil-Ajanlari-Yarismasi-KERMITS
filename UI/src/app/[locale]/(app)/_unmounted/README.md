# Unmounted pages

These pages are **not routed**. `_unmounted` is a Next.js private folder — the
leading underscore keeps the whole directory out of the router — and the files
inside are named `*.page.tsx` rather than `page.tsx`, so nothing here resolves
to a URL even if the folder convention changes.

They are kept, not deleted, for two reasons: remounting one must be a move and
a route-table line, never a rewrite; and their components are the app's worked
examples of Vision UI layout — reuse them, or take the pattern, when building
new pages.

Started 2026-08-18, when the app had accumulated more nav entries than it had
real pages. Pages move in and out of here regularly — this folder and the
commented entries in `src/vision/routes.js` are two halves of the same switch,
so **whichever `*.page.tsx` files are sitting here right now are the unmounted
set**. Do not trust a list written in prose, including this one; `ls` the folder.

`/compare` is the landing page and has stayed so through every swap: `/` and the
post-login redirect in `src/app/[locale]/login/page.tsx` both point there,
regardless of what else is mounted.

## What is still live

Nothing here was deleted from anywhere else:

- Everything under `src/vision/layouts/` is untouched — `dashboard`, `tables`,
  `billing` and their components — whichever of them is currently routed. The
  `*.page.tsx` files here are four-line wrappers around those layouts, which is
  why remounting one is a move and never a rewrite.
- `TopicPage` (`src/components/layout/`) is untouched — `finansman.page.tsx` is
  its only caller, but it is the generic category-to-produced-components
  renderer. Everything below it — `CategoryComponents`, `TableWidget`,
  `ProducedTable` — is still live via `/compare`, `/urunler` and
  `/kampanyalar`.
- `BankRegistry` (`src/components/widgets/`) is untouched — `banks.page.tsx` is
  the only caller, but the widget is the reusable half.
- The `nav.dashboard`, `nav.banks`, `dashboard.*` and `banks.*` message keys are
  still in `messages/en.json` and `messages/tr.json`, so a remount needs no
  translation work.

This mirrors how RTL is already handled: no drawer entry, no route, layout kept
at `src/vision/layouts/rtl/`.

## Remounting one

1. Move the file back and rename it: e.g.
   `_unmounted/tables.page.tsx` → `../tables/page.tsx`.
2. Add its entry back to `src/vision/routes.js` — the removed entries are left
   in place there as commented-out blocks, icon import included.
3. If it should be the post-login landing page, repoint the two redirects in
   `src/app/[locale]/login/page.tsx` and `src/app/[locale]/page.tsx`, which now
   both point at `/compare`. Keep the two in step with each other.
