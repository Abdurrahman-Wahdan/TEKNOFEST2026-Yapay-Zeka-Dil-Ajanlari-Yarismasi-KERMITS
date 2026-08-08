# The `banks/` package — design

What was built, why it is shaped this way, and how to change it. The ask is in
[BANK_TOOLS_BRIEF.md](BANK_TOOLS_BRIEF.md); this is the answer, and it is the
document to read before touching `banks/`.

Ten banks, seven tools, 3 700 lines. Endpoint contracts live in
`docs/discovery/captured/<bank>.md` with a `verify_<bank>.py` beside each —
**265 checks, all passing**. This package is the layer that turns those
contracts into something the chatbot can call.

---

## 1. The one decision everything else follows from

**Adding a bank must not add a tool.**

A tool is named after the *question*, not the bank. "What is the instalment?" is
one question whether it is asked of Kuveyt Türk or Ziraat, so there is one
`finance_quote` tool and `bank` is a parameter of it.

```
per-bank tools (rejected)              bank as a parameter (built)
─────────────────────────              ───────────────────────────
kuveytturk_finance_quote               finance_quote(bank, …)
kuveytturk_profit_share_quote          profit_share_quote(bank, …)
kuveytturk_card_quote                  card_installment_quote(bank, …)
kuveytturk_exchange_rates              exchange_rates(bank, …)
kuveytturk_convert                     convert_currency(bank, …)
albaraka_finance_quote                 list_products(bank, category)
albaraka_profit_share_quote            list_banks()
… × 10 banks
                                       ───────────────
≈ 45–70 tools. Unusable prompt.        7 tools. Bank #11 adds none.
Every new bank is a rewrite.
```

The corollary is what makes it work: **every bank-specific difficulty must be
absorbed below the tool layer.** A WAF, a CSRF token, a Turkish decimal comma, a
catalogue that lies — none of it may reach the model, because the model has one
signature for all ten banks.

---

## 2. Layout

```
banks/
├── __init__.py          re-exports the public surface
├── factory.py           get_bank(name), list_banks()
├── models.py            the return types every bank maps onto
├── parse.py             money() / rate() / fold() — one parser for all banks
├── http.py              clients, retries, CSRF tokens, empty-body handling
├── tools.py             the seven @tool functions the agent binds
└── providers/
    ├── base.py          BaseBank, UnsupportedProduct, capability contract
    ├── __init__.py      BANKS list + get_provider() + clear_catalogue_cache()
    ├── kuveytturk.py    ┐
    ├── albaraka.py      │
    ├── vakif.py         │  eight banks with endpoints
    ├── emlak.py         │
    ├── dunya.py         │
    ├── ziraat.py        │
    ├── turkiyefinans.py │
    ├── hayat.py         ┘
    ├── tom.py           two banks with none — still registered
    └── adil.py
```

This mirrors `llm/`, `embeddings/` and `vector_stores/` on purpose: factory +
`providers/{base, __init__, <name>}`. Learn one and you know all four. The four
extra modules (`models`, `parse`, `http`, `tools`) exist because there is no
LangChain type for a finance quote and because ten banks disagree about
everything.

### Call path

```
model decides            finance_quote(bank="vakif", product="ihtiyaç finansmanı", …)
      │
      ▼
banks/tools.py           @tool wrapper
                           • catches ValueError → returns a plain sentence
                           • formats the dataclass → compact JSON
      │
      ▼
banks/factory.py         get_bank("vakif") → the Vakif instance from BANKS
      │
      ▼
banks/providers/vakif.py   • find_product("ihtiyaç finansmanı") → "IF"
                           • fetch the page's anti-forgery token
                           • POST params in the query string, token in the body
                           • money("7.159,22 TL") → 7159.22
      │
      ▼
banks/http.py            httpx │ httpx+CSRF │ curl_cffi, one cached client each
      │
      ▼
                         www.vakifkatilim.com.tr
```

---

## 3. The seven tools

| tool | arguments | returns |
|---|---|---|
| `list_banks` | — | every bank, what it publishes, why it publishes no more |
| `list_products` | `bank`, `category` | codes, Turkish names, amount/term limits, currencies, rate |
| `finance_quote` | `bank`, `product`, `amount`, `term`, `include_schedule` | instalment, total, profit rate, annual cost rate, fees, and the payment plan on request |
| `profit_share_quote` | `bank`, `product`, `amount`, `term`, `currency`, `term_unit` | ratio, gross/net profit, gross/net annual rate |
| `exchange_rates` | `bank`, `codes` | buy, sell, unit and as-of per currency |
| `card_installment_quote` | `bank`, `card`, `amount`, `installments` | instalment, total, profit rate |
| `convert_currency` | `bank`, `source`, `target`, `amount` | result, rate, **`derived`** |

`convert_currency` is the seventh and was added by decision: it is the only home
for the brief's one agreed arithmetic exception (Kuveyt Türk and Hayat publish
gold and FX rates but no converter) and for the four banks that do convert
server-side. `leasing_quote` was deliberately not added.

`tools.py` holds fifteen `def`s, and only seven are tools. The rest are seven
`_`-prefixed formatters that turn a dataclass into a dict, plus `build_tools()`.
A function is a tool if and only if it carries `@tool`.

### Three rules the tools follow

**1. Docstrings are prompt text, not documentation.** The docstring becomes the
tool `description` sent to the model verbatim — that string is the whole basis on
which the model chooses. Hence English prose seeded with the Turkish words a user
would actually type (`finansman`, `kâr payı`, `döviz kuru`, `taksit`), a
statement of what comes back, and the valid bank names.

**2. The bank list is templated, not typed.** Docstrings contain a literal
`{banks}` and are filled in at import time from the live registry:

```python
_NAMES = ", ".join(_list_banks())
for _tool in _TOOLS:
    _tool.description = _tool.description.format(banks=_NAMES)
```

This is what keeps "adding a bank changes no tool" literally true —
`test_every_tool_description_names_the_live_banks` fails if a `{banks}`
placeholder survives or a registered bank is missing.

**3. A refusal is a sentence, not an exception.** Every tool body runs through
`_answer()`, which separates the two ways a call can fail:

```python
def _answer(build):
    try:
        return json.dumps(build(), ensure_ascii=False, separators=(",", ":"))
    except ValueError as exc:          # a refusal — says something useful
        return str(exc)
    except Exception as exc:           # our bug — still must not end the turn
        logger.exception("Tool failed unexpectedly")
        return f"That lookup failed unexpectedly ({type(exc).__name__})…"
```

A traceback ends the agent's turn. A sentence lets it tell the user why and carry
on. `UnsupportedProduct` subclasses `ValueError` precisely so one `except` covers
both refusals and bad input; anything else is a shape we did not expect from a
bank, so it is logged at `exception` level and still answered in words. Callers
can tell a refusal from a result because a refusal is not valid JSON.

Returns are compact JSON with `ensure_ascii=False` so Turkish survives. Fields
the bank left blank are dropped rather than sent as nulls. The payment schedule
is off by default — a 120-month plan would swamp the prompt for a question about
the monthly figure — and `finance_quote(include_schedule=True)` returns it for
"ödeme planını göster", which is the question that needs it.

---

## 4. What the model actually receives

```json
{
  "type": "function",
  "function": {
    "name": "finance_quote",
    "description": "Get a financing instalment quote from a bank's own calculator.\n\nUse for questions about finansman, ihtiyac finansmani, konut finansmani, arac/tasit finansmani, taksit. Valid banks: kuveytturk, albaraka, vakif, emlak, dunya, ziraat, turkiyefinans, hayat, tom, adil. `product` accepts the Turkish product name as it appears in list_products, or the product code; you do not need to know the code…",
    "parameters": {
      "properties": {
        "bank":    {"type": "string"},
        "product": {"type": "string"},
        "amount":  {"type": "number"},
        "term":    {"type": "integer"}
      },
      "required": ["bank", "product", "amount", "term"]
    }
  }
}
```

Types are inferred from the Python annotations. Verify what is really being sent
with:

```python
from langchain_core.utils.function_calling import convert_to_openai_tool
from banks import build_tools
[convert_to_openai_tool(t) for t in build_tools()]
```

---

## 5. Usage

```python
from llm import get_llm
from banks import build_tools, get_bank, list_banks

model = get_llm().bind_tools(build_tools())     # the agent path

get_bank("vakif").finance_quote("ihtiyaç finansmanı", 100_000, 24)   # direct
list_banks()                                     # ten banks + capabilities
```

### A real two-step trace

> "Kuveyt Türk ile Vakıf Katılım arasında 100.000 TL 24 ay ihtiyaç finansmanı
> hangisi daha ucuz?"

```
STEP 1  two parallel calls
  list_products {bank: "kuveytturk", category: "finance"}  → 19 products
  list_products {bank: "vakif",      category: "finance"}  → 7 products

STEP 2  two parallel calls
  finance_quote {bank:"kuveytturk", product:"İhtiyaç Finansmanı", amount:100000, term:24}
    → {"monthly_installment":7490.78,"total_payable":179779.14,"monthly_profit_rate":4.11,…}
  finance_quote {bank:"vakif", product:"İhtiyaç Finansmanı", amount:100000, term:24}
    → {"monthly_installment":7159.22,"total_payable":171821.36,"monthly_profit_rate":3.75,…}

STEP 3  no tool calls — the model writes the comparison table
```

What this demonstrates:

- The model **discovered the product names itself**. It did not know İhtiyaç
  Finansmanı is `SAGLIKFINANSMANI` at Kuveyt Türk and `IF` at Vakıf; it called
  `list_products`, then passed the Turkish name, and `find_product` resolved it
  per bank.
- **One tool, two banks, only `bank:` changed** — while underneath one call was a
  JSON POST to a hashed URL and the other fetched a CSRF token and posted a form.
- The arithmetic in the final answer is **comparison**, not pricing. Both
  instalments came from the banks.

---

## 6. Return types (`models.py`)

Frozen dataclasses. Every bank maps its own field names onto these, so the agent
sees one shape regardless of bank. `raw` is kept on every type: when a bank
returns something we did not model, it stays reachable without a code change.

| type | notes |
|---|---|
| `Product` | `code, name, category, min/max_amount, min/max_term, currencies, rate, raw` |
| `PaymentRow` | one line of a schedule |
| `FinanceQuote` | instalment, total, `profit_rate`, `annual_cost_rate`, `fees`, `schedule` |
| `ProfitShareQuote` | `ratio` (nullable), gross/net profit, gross/net annual rate, `term_unit` |
| `Rate` | `buy`, `sell`, `unit` (`"gram"` for metals) |
| `CardInstallmentQuote` | instalment, total, profit rate |
| `Conversion` | `amount`, `result`, `rate`, **`derived`** — all `Decimal` |

Three field decisions worth remembering:

- **`Product.raw` is not optional.** Requests cannot be built without it. Kuveyt
  Türk needs `ProductGroup`, `FEC` and the exact `Title`; Albaraka echoes the
  whole catalogue blob back as its `FinanceType`; Dünya needs its `category`;
  Ziraat needs the rate from `get-vade` handed back to the calculation.
- **`ProfitShareQuote.ratio` is nullable.** Kuveyt Türk publishes a participation
  ratio; Albaraka, Vakıf, Emlak, Dünya and Hayat publish only the resulting
  rates. `None` says "not published" where `0.0` would say "zero".
- **`Conversion.derived`** distinguishes a figure the bank calculated from one we
  multiplied out of its quoted rate. Money is `float` everywhere else because the
  endpoints return floats and we never do arithmetic on them; `Conversion` uses
  `Decimal` because it is the one place we do.

---

## 7. The bank interface (`providers/base.py`)

**Nothing is abstract.** Every method refuses by default, naming what the bank
does publish, and a provider overrides only what its bank really answers.

```python
class BaseBank(ABC):
    name: str                      # "vakif"
    display_name: str              # "Vakıf Katılım Bankası"
    capabilities: frozenset[str]   # {"products", "finance", ...}
    transport: str = "httpx"       # httpx | csrf | impersonate | none
    notes: str = ""                # why it publishes no more, in a sentence
```

That choice is what lets Adil and T.O.M. be registered banks with no endpoints
and no stub methods — the brief forbids empty methods returning `None`, and
writing two files of methods that only raise would have been the same mistake in
a different shape. It also means a gap is always a sentence rather than a crash.

### Capabilities are a promise

| capability | method it promises |
|---|---|
| `products` | `products(category)` |
| `finance` | `finance_quote(...)` |
| `profit_share` | `profit_share_quote(...)` |
| `card` | `card_installment_quote(...)` |
| `rates` | `rates()` |
| `convert` | `convert(...)` |

`test_every_capability_is_really_implemented` asserts the mapping holds in both
directions: declaring without implementing would refuse while claiming to work;
implementing without declaring would work while `list_banks` says it cannot.

An override that only makes a refusal *more useful* is marked `@refusal` and does
not count as a capability. Türkiye Finans uses this — it cannot state an
instalment, but its refusal names the monthly profit rate and annual cost rate
the bank does publish for the term asked about.

### Transports are declared, never hardcoded

| value | meaning | banks |
|---|---|---|
| `httpx` | plain | Kuveyt Türk, Ziraat, Türkiye Finans, Hayat |
| `csrf` | httpx + a per-page anti-forgery token | Vakıf, Dünya |
| `impersonate` | curl_cffi, for WAFs that fingerprint the TLS handshake | Albaraka, Emlak |
| `none` | nothing to call | T.O.M., Adil |

`BaseBank._json/_text/_token` inject the right transport, so no provider names
one at a call site. `curl_cffi` is never the default: it is slower, and httpx is
the project's client everywhere else. The health checker reads `transport` to
know which banks are cheap to poll.

### Turkish product resolution

`find_product(category, query)` accepts a code or a Turkish name and never makes
the model learn a bank's internal vocabulary. Matching goes through
`parse.fold()`, which folds both sides to bare ASCII alphanumerics, because
Turkish casing does not round-trip: `"İ".lower()` leaves a combining dot, and a
model typing `IHTIYAC FINANSMANI` means `İhtiyaç Finansmanı`.

Order: exact code → exact name → unique substring → raise, listing what the bank
offers. An ambiguous substring raises rather than guessing.

---

## 8. `http.py`

One cached client per transport, with a lock and a `clear_http_cache()`, mirroring
`vector_stores/client.py`.

- `request_json(...)` returns **`None` for an empty body**. A zero-length 200 is
  how Vakıf says "this term is not offered"; decoding it would raise a JSON error
  that reads like a broken endpoint.
- `retry_if(payload)` owns the retry policy. Kuveyt Türk's finance endpoint
  intermittently answers 200 with an empty `Meta`; one retry separates a flaky
  call from a genuinely bad product.
- Non-2xx raises `ValueError` **carrying the bank's own message** where it sends
  one, so an out-of-range term reaches the user as
  `"Lütfen 31 değerine eşit ya da daha büyük bir değer giriniz."`
- `csrf_token(page_url, refresh=False)` fetches and caches the anti-forgery
  token; providers retry once with `refresh=True` when a token stops being
  accepted.
- **No user-agent is set when impersonating.** curl_cffi sends one matching the
  TLS fingerprint it presents, and a mismatched pair is rejected again — as a
  JSON decode error, not an obvious block.

TLS verification stays **on**. Five of the discovery scripts pass
`verify=False`; every host was re-checked and verifies cleanly, so the package
does not copy that.

---

## 9. How to add a bank

One new file, one list entry, no tool changes.

1. **Map and verify it first.** Write `docs/discovery/captured/<bank>.md` and
   `docs/discovery/verify_<bank>.py`, and get it passing. A provider written
   against a guess cannot be tested and will not be correct.
2. **Write `banks/providers/<bank>.py`.** Subclass `BaseBank`; set `name`,
   `display_name`, `capabilities`, `transport`, and `notes` if it publishes less
   than the others. Override only the methods its bank really answers. Reach the
   network through `self._json` / `self._text` / `self._token`, never `httpx`
   directly. Parse numbers with `banks.parse`.
3. **Append an instance to `BANKS`** in `providers/__init__.py`.
4. **Record fixtures** into `tests/fixtures/banks/<bank>/` — real payloads from
   the live endpoints, including at least one "not offered" response.
5. **Add unit tests** for its parsing and its refusals, and add its name to the
   `ALL_BANKS` tuple and the relevant `parametrize` lists in the integration
   tests.

Settings are the exception that should stay empty: none of these endpoints
authenticate, so no bank should ever add a settings field. The three that exist
(`BANK_HTTP_TIMEOUT`, `BANK_HTTP_RETRIES`, `BANK_USER_AGENT`) are all any bank
needs.

## 10. How to add a tool

Rarer, and the bar is higher: **a new tool means a new kind of question**, not a
new bank or a new product. Before adding one, check that the question is not
already `list_products` plus an existing quote tool.

1. Add the method to `BaseBank` with a body that raises `self._unsupported(...)`,
   so every bank refuses it until it implements it.
2. Add the capability name to `CAPABILITY_METHODS` in `providers/base.py`. The
   consistency test now covers it.
3. Implement it on the providers whose banks answer it, and add the capability to
   their `capabilities`.
4. Add the `@tool` function to `tools.py` and a `_`-formatter for its return type.
   Put `{banks}` in the docstring where the valid bank names belong. Route the
   body through `_answer(...)`.
5. Append it to `_TOOLS`. `build_tools()` and the description templating pick it
   up automatically.
6. Update `test_the_tool_set_is_fixed_and_names_a_bank_as_an_argument`, which
   asserts the exact list — it is meant to fail, so that growing the tool set is
   a deliberate act rather than a drift.

Every tool except `list_banks` must take `bank` as its first argument. That is
the invariant the whole design rests on, and the same test enforces it.

---

## 11. Traps this package already pays for

Each one produced a wrong answer or a wasted day before it was handled.

- **A quote has to add up, and that is checked once for every bank.**
  `BaseBank._check_quote` and `_check_profit_share` run on every quote before it
  is returned: the total must exceed the advance, the plan must match the term,
  the instalment must be positive, the profit must be at least half of one month
  at the rate the bank reported, and a profit-share figure must follow from the
  bank's own annual rate over the term. These are consistency checks on the
  bank's own numbers against each other — nothing is priced or substituted.

  The thresholds are set from measurement, not taste. Across the six banks that
  quote, a 24-month plan returns 14–20× one month's profit, so a 0.5× floor has
  two orders of magnitude of headroom; and every profit-share figure agrees with
  its own annual rate to **0.0%**, so a 15% tolerance only catches an
  order-of-magnitude contradiction. This is what turns "find each bad number by
  hand against a live endpoint" into "the tool will not return a number that
  does not add up" — it would have caught the Ziraat 0,16 TL case and Hayat's
  term-independent account without either being known about first.
- **A bank's own declared limit is a limit.** `BaseBank._check_limits` refuses
  what a catalogue says the bank will not price, because several of them answer
  past their own ceiling with an arithmetically consistent figure that nothing
  downstream can catch. Dünya declares a 12 000 000 ceiling and quotes
  50 000 000; Vakıf's card declares 1–12 instalments and answers 99. Worst was
  Ziraat: on its 124 999 band, 200 000 TL came back as a 200 000,16 total —
  every schedule row principal-only, 0,16 TL of profit — while reporting a 4,99%
  rate. Only what a bank actually declares is checked; Vakıf and Emlak publish
  no amount ceiling anywhere, so nothing can invent one for them.
- **A guard that only runs on the ambiguous path is not a guard.** Ziraat's
  band-fit check used to be skipped whenever the query named one product
  exactly — and `list_products` teaches the model the exact names, so that was
  the likely path, not the rare one. It now runs on both.
- **A repeated code is ambiguous, not first-wins.** Kuveyt Türk lists
  ELKTRARACSARJUNITE twice, as Bisiklet Finansmanı and Elektrikli Araç Şarj
  Ünitesi, whose real term ceilings are 36 and 1. Returning the first made the
  second unreachable and quoted the wrong limit for it. `MaturityTerm`, not
  `MaturityTermMax`, is the entry's real ceiling.
- **A term must reach the band the user meant.** Vakıf, Emlak and Dünya price a
  fixed list of month-labelled terms (31 / 91 / 180 / 364 / 366 days). Taking the
  nearest band *at or below* the request looks reasonable and is badly wrong: a
  year is 360 days, falls short of 364, lands on the six-month band, and returns
  about **44% of the right figure** as a confident, well-formed quote with only
  `term: 180` to hint at it. `BaseBank._band` takes the nearest band in either
  direction, treats the last band as open-ended, and **refuses** anything no band
  comes within `BAND_TOLERANCE` (15%) of — so an unqualified `12` is refused
  rather than quietly priced as 31 days.
- **Not every account is priced by term.** Hayat's Avantajlı Günlük Hesap returns
  one day's profit whatever `MaturityTerm` is sent — 32, 60, 90 and 365 days all
  come back 79,95 TL on 100 000. The returned profit is checked against the
  bank's own stated annual rate over the requested term, and a figure that does
  not follow from it is refused instead of being relabelled.
- **Converting a currency to itself is not a conversion.** Applying the buy/sell
  spread against one currency turned 10 USD into 9,09 USD. Short-circuited
  before any request, since there is no rate to look up.
- **Filtering rates needs the alias map.** Kuveyt Türk and Hayat call gold
  "ALT (gr)"; comparing a requested `XAU` against that matches nothing, and the
  answer reads as "this bank does not quote gold" while quoting it on the row
  above. `find_rates` resolves through `rate_aliases`, and conversions answer in
  the codes the caller asked about rather than the bank's internal names.
- **Kuveyt Türk's profit share counts days, always.** Its `p10` day/month flag is
  inert — `p3=12` returns the same 12-day profit either way. Reading it as months
  understates a year by about thirty times, silently. Months are sent as 30-day
  multiples, and because a month is 30 days for Ara Dönem (which answers 31 with
  zeros) and 31 for plain Katılma (which answers exactly 30 with zeros), both are
  offered and the bank picks.
- **Dünya strips every separator instead of parsing it.** `"1000.0"` is read as
  10 000 and `"10,5"` as 105, each answered with a plausible figure and no error.
  Amounts go out as bare integers, fractions are refused, and the `sourceAmount`
  the bank echoes back is checked against what was asked.
- **"No data" has four shapes, none an HTTP error**: 200 with all-zero fields,
  200 with an empty body, 200 with an `errorMessage` inside the JSON, and 404
  with an empty body. A check reading only the status code calls all four
  healthy. **A zero is never a price.**
- **Hayat's 50 000 TL floor is checked before the call**, so someone below it is
  told the minimum rather than quoted "0 TL profit".
- **Product identity is rarely just a code.** Albaraka repeats `ProductCode`
  across nine products (identity is the campaign code); Türkiye Finans repeats
  `Code` across `CreditID`s with different fees; Ziraat lists the same product
  once per term band with a ceiling that *falls* as the term rises, so
  `finance_quote` picks the band from the amount and term — and refuses to swap
  "İhtiyaç Finansmanı" for "İhtiyaç Finansmanı Hac / Umre", which merely shares a
  prefix.
- **Kuveyt Türk's `p8` title is not cosmetic.** Two entries share
  `ELKTRARACSARJUNITE`; the endpoint validates the term against the entry named
  in `p8`.
- **Cards over-promise.** Sağlam Kart Troy declares 12 instalments and 404s above
  9. The declared limit is reported; the endpoint decides, and the refusal says
  to ask for fewer.
- **Albaraka's catalogue regex.** The attribute is single-quoted around
  HTML-escaped JSON, so the obvious double-quote pattern matches nothing and
  reads as "this bank has no products".
- **Catalogues expire.** Two of them carry live rates inside — Türkiye Finans's
  profit-share table and Ziraat's per-product rate — so a long-running process
  would serve yesterday's rate as today's. `CATALOGUE_TTL_SECONDS` is 15
  minutes. It does not corrupt quotes (Ziraat's endpoint ignores the rate we
  pass back), only what is reported.
- **Endpoint URLs stay in the log.** They carry each bank's opaque calculator
  hash, and a refusal is text a user may read.
- **Rates and amounts are formatted differently in one response.** Albaraka
  states `GrossRate` with a dot for its decimals and `IncomeTax` with a comma;
  parsing a rate with `money()` turns `36.731684` into `36731684`. Hence separate
  `money()` and `rate()`.

---

## 12. Tests

```bash
pytest tests/unit -q          # 133 bank tests, no network
pytest -m integration -q      # 61 bank cases, live endpoints
python docs/discovery/verify_<bank>.py     # the contract itself, per bank
```

**Unit tests replace the transport at one seam.** Every provider reaches the
network through `BaseBank._json` / `_text` / `_token`, so patching
`banks.providers.base` covers all ten banks. Fixtures in
`tests/fixtures/banks/<bank>/` were recorded from the live endpoints — the probe
captures in `docs/discovery/captured/*_full.json` are unusable for this, because
they truncate every response at 6 000 characters and the larger ones are not
valid JSON. A `no_network` helper asserts that the paths which must not make a
request — Adil, T.O.M., Ziraat's browser-only calculators, Hayat's minimum
balance — really do not.

**Integration tests assert the contract, never an exact number**: field present,
type right, value in a sane range. Rates change daily and that change is not a
failure. Known bank-side gaps are asserted as *refusals*, not skipped.

Three structural tests are worth protecting:

- `test_every_capability_is_really_implemented` — capabilities cannot drift from
  behaviour.
- `test_declared_capabilities_hold_against_the_live_banks` — the same claim,
  against the real endpoints.
- `test_the_tool_set_is_fixed_and_names_a_bank_as_an_argument` — the seven names
  and the `bank` parameter.

Every module-level cache exposes a `clear_*` called from `tests/conftest.py`:
`clear_http_cache()` and `clear_catalogue_cache()`.

---

## 13. Deviations from the brief, and why

| brief | built | reason |
|---|---|---|
| `Product` without `raw` | `Product.raw` required | requests cannot be built without the catalogue entry |
| "one shared httpx client" | one client **per transport** | Albaraka's and Emlak's WAFs reject httpx entirely |
| six tools | seven | `convert_currency` is the only home for the agreed gold exception and the four server-side converters |
| `ratio: float` | `float \| None` | five banks publish rates, not a participation ratio |
| `leasing_quote` "if free" | not added | Kuveyt Türk has leasing; Ziraat's is browser-only. Left documented, not built |
| fixtures from `kuveytturk_full.json` | freshly recorded | that file truncates at 6 000 chars and is not valid JSON |

---

## 14. Current state

| bank | publishes | transport | catalogue |
|---|---|---|---|
| Kuveyt Türk | finance, profit share, card, rates, convert | httpx | an endpoint, five `p1` values |
| Albaraka | finance, profit share, rates, convert | curl_cffi | page HTML, echoed back verbatim |
| Vakıf | finance, profit share, card, convert | httpx + CSRF | page `<option>` values |
| Emlak | finance, profit share | curl_cffi | page `<option>` values |
| Dünya | finance, profit share, convert | httpx + CSRF | homepage HTML, JSON blobs |
| Ziraat | finance | httpx | an endpoint, per product |
| Türkiye Finans | products only | httpx | a table service |
| Hayat | profit share, rates, convert | httpx | none — three account types |
| T.O.M. | nothing | none | — |
| Adil | nothing | none | — |

Three banks are worth knowing about individually:

- **Türkiye Finans publishes tables, not answers.** Its calculator does the
  annuity in the browser, so an instalment would have to be ours. It declares
  `products` only, and its refusals name the rates it does publish.
- **Ziraat's kâr payı and leasing are browser-only.** The Drupal form answers 493
  to every non-browser client, curl_cffi included, and no JSON route exists.
  Declared, so the refusal costs no request.
- **T.O.M. and Adil publish nothing, for different reasons.** Same answer to a
  user today; different remedies. T.O.M. becomes available the day someone
  supplies a partner credential, Adil has nothing to integrate. `notes` carries
  the distinction.

## 15. What is not built

- **No health checker.** `transport` and `capabilities` exist for it, and the
  `verify_<bank>.py` scripts are its assertions, but nothing runs them daily yet.
  Adil and T.O.M. must stay out of it: there is no endpoint to watch, and a 401
  polled every morning trains people to ignore alerts.
- **No agent or graph.** `build_tools()` is ready for
  `get_llm().bind_tools(...)`; nothing binds it in the repo yet, and neither
  does the system prompt or the Turkish output conventions.
- **No cross-bank comparison tool.** "Hangi bankada en uygun?" is the question
  this exists to answer, and today it is N sequential `finance_quote` calls with
  a different product name per bank. A `compare_finance` tool fanning out in
  parallel is the obvious next tool — the first one that would earn an eighth
  slot.
- **No leasing.** Kuveyt Türk's leasing endpoint is verified in discovery across
  TL/USD/EUR and Ziraat's is browser-only, but there is no `leasing` capability
  and no tool, so leasing is unreachable for every bank. This was a scope
  decision, not an oversight — adding it means an eighth tool, a `LeasingQuote`
  model and a `leasing` capability.
- **No Hayat financing.** Its loan endpoint exists but rejects every payload
  shape tried, with no public calculator to observe a working request from.
  Recorded as unknown rather than guessed at.
- **Kuveyt Türk's first-instalment-date endpoint is not wired.** It only
  validates an optional `p7` we never send.
- **Emlak's amount ceiling is unknown.** It states no maximum on any page or
  endpoint and its calculator never refuses one — it quotes a billion lira as
  readily as a hundred thousand — so nothing can enforce a cap for it. Said so
  in its `notes`, which `list_banks` returns. Vakıf turned out to state its cap
  in its own error text ("Seçilen vade için 250.000 TL'ye kadar hesaplama
  yapılabilmektedir") and already refuses; Kuveyt Türk refuses likewise.
- **Türkiye Finans's rate bands overlap across account groups.** TL 32–91 gün is
  41,08% under group 4 and 28,80% under group 1. Both are real; the minimum
  amount is what separates them, so it is now part of the product name. The bank
  publishes no name for either group.
