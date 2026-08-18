# Unmounted components

Nothing here is imported by live code. These are kept, not deleted, for the same
reason as the pages in `src/app/[locale]/(app)/_unmounted/`: they are worked
examples worth reusing or taking the pattern from, and remounting one should be
a move plus an import, never a rewrite.

They were gathered here on 2026-08-18 while consolidating the table components.
The point of the folder is that `src/components/ui/`, `widgets/` and `layout/`
now contain only things that are actually wired up — so picking a component to
build with no longer means guessing which of two Buttons or two table
implementations is the live one.

Relative imports between files in here still resolve, because the whole cluster
moved together. `CompareFinance.tsx`'s import of `Button` was rewritten from
`@/components/ui/Button` to `./Button`; that is the only edit any of these files
received.

## What is here and why it is dead

- **`CompareFinance.tsx` + `.module.scss`** — an entire second table
  implementation, raw `<table>` markup styled by a CSS Module. The live app has
  exactly one table, `widgets/ProducedTable.tsx`. `compare/page.tsx` already
  described this file as unmounted; now it actually is.
- **`Button.tsx` + `.module.scss`** — its only importer was `CompareFinance`.
  Live code uses `ui/ActionButton.tsx` (MUI) and `VuiButton`.
- **`StatTile.tsx` + `.module.scss`** — zero importers.
- **`AppShell.tsx` + `.module.scss`** — the pre-Vision app shell, replaced by
  `vision/VisionApp.js` + the Vision Sidenav. Zero importers, and its nav list
  still points at routes that no longer exist.
- **`LocaleSwitch.tsx`, `ThemeToggle.tsx`, `Switcher.module.scss`** — imported
  only by `AppShell`, so the whole cluster came across together. Note the live
  theme toggles are `ui/ThemeToggleIcon.tsx` on the auth screens and
  `vision/components/VuiThemeToggle` inside the app; this is a fourth one.
- **`BankRegistry.module.scss`** — orphaned stylesheet. `BankRegistry.tsx`
  never imported it and **stays live** in `widgets/`, because
  `widgets/catalog.ts` still maps it: a producer emitting
  `type: "BankRegistry"` renders it.

## Remounting one

Move the file back to `ui/`, `widgets/` or `layout/`, fix the relative imports
it has to things left behind here, and import it. Nothing else references these
paths, so there is no route table or registry to update — unlike the unmounted
*pages*, which also need a `src/vision/routes.js` entry.
