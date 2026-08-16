# Component fixtures

Hand-written development data for `GET /api/components/{category}`, served until the RAG
producer lands. **Nothing in here was read from a bank.** The values are invented; they
exist so the UI can be built and tested against the real contract before the producer
exists. The API reports `source: "fixture"` and the UI badges it on screen, so placeholder
content is never mistaken for bank data.

One file per category, named for the route segment: `finansman.json`, `kartlar.json`, and
so on. A category listed in `routers/components.py::CATEGORIES` with no file here answers
`200` with an empty component list — "this page has no content yet" is an answer, and the
UI has a state for it.

## The contract

```jsonc
{
  "generated_at": "2026-08-14T09:00:00Z",   // ISO-8601, optional
  "source": "fixture",                       // "fixture" | "agent"
  "components": [                            // ordered; the order is the argument
    { "type": "table", "props": { … } }
  ]
}
```

`type` names a component in the frontend catalog (`UI/src/components/widgets/registry.tsx`).
An unknown type renders as a visible placeholder naming what was asked for — it is never
silently dropped. `props` is validated in TypeScript only; see
`UI/src/lib/contract.ts` for the authoritative schema.

## The table component

Only `rows` is genuinely required. Everything else has a defensible default, which is what
makes this easy to produce:

| Field | Required | Notes |
|---|---|---|
| `rows[].cells` | **yes** | object of `columnKey -> value` |
| `rows[].cite_url` | no | strongly wanted — makes every row traceable |
| `columns` | no | **omitted → inferred** from the union of row keys, in first-seen order |
| `columns[].type` | no | inferred from the values when absent |
| `id`, `title`, `subtitle`, `notes` | no | `title` is rendered raw, never through i18n |

Column types: `text` · `money` (+`currency`) · `percent` · `number` · `date` · `bank` ·
`link` · `badge` · `bool`. An unrecognised type is **ignored and inferred from the values
instead** — a column typed `currency` full of numbers still right-aligns and sorts
numerically — and the table records a warning rather than breaking. A cell missing from a
row renders as an em dash: absent means "not found", never zero.

Mark a column `filterable` to get a multiselect for it, and `sortable` to make its header
clickable. Bank columns and badge columns get a multiselect automatically.

## What `finansman.json` deliberately demonstrates

Four tables, each covering a shape the producer will eventually emit:

1. **`konut-basvuru-kosullari`** — 8 columns × 8 rows, every column type, full citations.
2. **`ihtiyac-vade-secenekleri`** — 3 columns × 30 rows, the long-and-narrow case.
3. **`gerekli-belgeler`** — **no `columns` key at all**, proving inference works.
4. **`masraf-kalemleri`** — rows with **missing cells**, proving partial data still renders.

Keep those four shapes represented as the fixtures grow. They are the cases that break
naive table components.
