# Brief — the `banks/` package and its agent tools

Build the layer that lets the chatbot answer live pricing questions by calling
participation-bank endpoints directly. **Kuveyt Türk and Albaraka are fully
mapped and verified** and are the banks to implement. Eight more follow, so the
shape matters more than the first implementation.

Read these first — they are the verified contracts, and the verify scripts are
working clients for every endpoint in them. Between them they answer almost
every question this brief raises:

- `docs/discovery/captured/kuveytturk.md` + `docs/discovery/verify_kuveytturk.py`
- `docs/discovery/captured/albaraka.md` + `docs/discovery/verify_albaraka.py`

`docs/ARCHITECTURE.md` and `docs/HANDOFF.md` give the house style and the
project rules.

## The one design decision that matters

**Adding a bank must not add a tool.** The agent gets a fixed, small set of
tools; each bank is a provider behind them. If tools were named
`kuveytturk_finansman_quote`, ten banks would mean forty-plus tools, an
unusable prompt, and a rewrite every time a bank is added.

So: **one tool per product category, `bank` is a parameter.** A new bank is one
new file plus one list entry, exactly like `llm/providers/`, and no tool
signature changes.

## Layout

Mirror the three existing factory packages — `llm/`, `embeddings/`,
`vector_stores/` are deliberately identical and this must be the fourth.

```
banks/
├── __init__.py            re-exports get_bank, list_banks, build_tools
├── factory.py             get_bank(name) -> BaseBank
├── models.py              the return types below
├── http.py                one shared httpx client: timeout, retry, UA
├── tools.py               the LangChain tools the agent binds
└── providers/
    ├── base.py            BaseBank ABC
    ├── __init__.py        BANKS list + get_provider()
    └── kuveytturk.py      the only provider for now
```

Follow the existing conventions exactly: module docstring with a usage example,
`get_provider()` picking the first provider whose `matches()` returns true,
settings in `config/settings.py` and nowhere else.

## Return types (`models.py`)

Plain dataclasses. Every bank maps its own field names onto these, so the agent
sees one shape regardless of bank. Keep `raw` — when a bank returns something we
did not model, we want it reachable without a code change.

```python
@dataclass(frozen=True)
class Product:
    code: str              # ECOMMERCE, HOSGELDIN, SK
    name: str              # "Alışveriş Finansmanı" — keep the Turkish
    category: str          # finance | profit_share | card | leasing | gold
    min_amount: float | None
    max_amount: float | None
    min_term: int | None
    max_term: int | None
    currencies: tuple[str, ...] = ("TRY",)

@dataclass(frozen=True)
class FinanceQuote:
    bank: str
    product: Product
    amount: float
    term: int
    installment: float
    total: float
    profit_rate: float          # monthly, as the bank states it
    annual_cost_rate: float | None
    fees: dict[str, float]      # allocation, survey, hypothec
    schedule: list[PaymentRow]
    raw: dict

@dataclass(frozen=True)
class ProfitShareQuote:
    bank: str; product: Product; amount: float; term: int; currency: str
    term_unit: str              # "day" or "month" — banks differ, see below
    ratio: float                # participation ratio, %
    gross_profit: float; net_profit: float
    gross_annual_rate: float | None; net_annual_rate: float | None
    raw: dict

@dataclass(frozen=True)
class Rate:
    code: str                   # USD, EUR, XAU_GRAM
    name: str
    buy: float; sell: float
    unit: str = "1"             # "gram" for metals
```

Also `CardInstallmentQuote` and `LeasingQuote` — same pattern, fields listed in
the Kuveyt Türk contract doc.

Money stays `float` here because that is what the endpoints return and we never
do arithmetic on it. The one exception is gold (below), which uses `Decimal`.

## The bank interface (`providers/base.py`)

```python
class BaseBank(ABC):
    name: str                  # "kuveytturk"
    display_name: str          # "Kuveyt Türk Katılım Bankası"
    capabilities: frozenset[str]   # {"finance", "profit_share", "rates", ...}

    @abstractmethod
    def products(self, category: str) -> list[Product]: ...

    @abstractmethod
    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote: ...

    @abstractmethod
    def profit_share_quote(self, product: str, amount: float, term: int,
                           currency: str = "TRY") -> ProfitShareQuote: ...

    @abstractmethod
    def rates(self) -> list[Rate]: ...
```

A bank that cannot do something raises `UnsupportedProduct`, and the tool turns
that into a plain sentence for the agent. Do **not** add empty methods that
return `None` — silent nothing is indistinguishable from a broken endpoint, and
that mistake has already cost this project days.

Declare `capabilities` honestly. Adil Katılım has no calculator at all, and
"this bank does not publish this" is a legitimate answer the agent must be able
to give.

## The tools (`tools.py`)

LangChain `@tool` functions, bound with `get_llm().bind_tools(...)`. Six of
them, and they stay six as banks are added:

| tool | arguments |
|---|---|
| `list_banks` | — |
| `list_products` | `bank`, `category` |
| `finance_quote` | `bank`, `product`, `amount`, `term` |
| `profit_share_quote` | `bank`, `product`, `amount`, `term`, `currency` |
| `exchange_rates` | `bank`, `codes` (optional filter) |
| `card_installment_quote` | `bank`, `card`, `amount`, `installments` |

Add `leasing_quote` only if it costs nothing to add; it is the least-used.

Tool docstrings are prompt text — the model reads them to choose. Write them in
English, state what the tool returns, and say which bank names are valid. Return
compact structured text or JSON, not prose: the agent writes the prose.

Turkish product vocabulary must survive into the arguments. Users ask for
"ihtiyaç finansmanı", "kâr payı", "döviz kuru", "taksit". Accept the Turkish
product name as well as the code and resolve it against `products()` — do not
force the model to know that İhtiyaç Finansmanı is `SAGLIKFINANSMANI`, because
it does not, and that mapping is bank-specific anyway.

## Rules that are not negotiable

These come from the project owner and from mistakes already paid for.

1. **Never compute a price ourselves.** The endpoint's number is the truth. No
   reverse-engineered annuity formulas, no fallbacks. One exception, agreed
   explicitly: **gold**, where the bank's own page multiplies rate × grams in
   the browser and there is no endpoint to call. Do that multiplication in
   `Decimal`, and label the result as derived from the quoted rate.
2. **Everything in English** — code, comments, docstrings. Turkish only inside
   data values, where it belongs.
3. **Write only what we use.** No stub providers, no unused settings fields, no
   placeholder banks. Nine empty bank modules would be worse than one real one.
4. **A zero is not a price.** Kuveyt Türk's profit-share endpoint answers `200`
   with every field `0.0` when the product and currency disagree. Treat an
   all-zero response as a failure and raise, never return it as a quote.
5. **Short and plain.** Over-engineering is explicitly unwelcome. No plugin
   registry, no dynamic import magic, no async unless something needs it.

## Kuveyt Türk specifics you will hit

All of these are verified, and all of them broke something first.

- Base URL is `https://www.kuveytturk.com.tr/ck0d84?<hash>`, a **different hash
  per calculator**. It looks like a tracking beacon; it is the calculator. The
  hashes are in the contract doc. Keep them in the provider module as named
  constants, not scattered literals.
- Required headers on every call: `x-requested-with: XMLHttpRequest`,
  `x-bone-language: TR`, `accept: application/json`, and a `referer` pointing at
  the matching calculator page. No cookies, no session, no CSRF token.
- **One catalogue endpoint feeds everything.** `…&p1=LoanCalculator` and four
  other `p1` values return every product code plus its amount and term limits.
  `products()` should call it; do not hardcode 19 product codes. Cache it in
  memory for the process, since it changes about as often as the product range.
- Its metadata is **not** fully trustworthy: Sağlam Kart Troy declares 12
  instalments and 404s above 9; Ara Dönem declares month terms and only answers
  in days. Validate against limits, but let the endpoint have the final word.
- Profit share takes days or months in the same field with `p10` choosing which.
  Day mode is the common case. Per-product terms are tabulated in the contract.
- Yuvam returns zeros for every input, on their site too. Mark it unavailable
  rather than retrying it.
- The finance endpoint intermittently returns `200` with an empty `Meta`. One
  retry is enough; `http.py` should own that policy.

## `http.py` must handle two transports, not one

This is the second-biggest design constraint after the tool shape, and it is not
optional.

Kuveyt Türk works with plain `httpx`. **Albaraka does not.** It runs an F5 WAF
that answers `200` with an HTML "Request Rejected" page for any `/plugins/`
call that does not come from a real browser — and it is not header-based.
Full Chrome headers, `adrum: isAjax:true`, a warmed cookie jar and HTTP/2 are
all still rejected, because it fingerprints the TLS handshake.
`curl_cffi` impersonating Chrome passes unchanged and returns byte-identical
numbers. It is already in `requirements.txt`.

So `http.py` exposes one `get`/`post` and each provider declares which transport
it needs:

```python
class BaseBank(ABC):
    transport: str = "httpx"     # or "impersonate" for WAF-guarded hosts
```

Do not make `curl_cffi` the default for everything "just in case" — it is
slower, and `httpx` is the project's HTTP client everywhere else. Do not
hardcode the choice inside the Albaraka provider either; a third bank will need
it and the health checker has to know which banks are cheap to poll.

Expect more of this. The handoff notes Dünya times out under `httpx` while
loading fine in a browser, which is likely the same thing.

## Two banks are mapped, not one

Albaraka is verified too — `docs/discovery/captured/albaraka.md` and
`verify_albaraka.py`, 35/35 passing. Build against **both**, because they
disagree in exactly the ways the interface has to absorb:

| | Kuveyt Türk | Albaraka |
|---|---|---|
| transport | `httpx` | `curl_cffi` (WAF) |
| catalogue | an endpoint, 5 `p1` values | **embedded in page HTML** as `<option value='…'>` JSON |
| product identity | `ProductCode` | `(ProductCode, ProjectCode, CampaingCode)` — six products share one `ProductCode` |
| numbers | JSON floats (`6684.28`) | **formatted Turkish strings** (`"6.684,28 TL"`, `"% 64,46"`) |
| currency conversion | rates only, we multiply | **server-side**, returns the converted number |
| profit-share term | days or months via a flag | `Period=MONTH\|DAY` |

Two consequences for `models.py`: parsing Turkish-formatted money belongs in one
shared helper, not in each provider; and `Product.code` cannot be assumed
unique, so key products by a bank-supplied opaque id and keep the raw catalogue
entry on the object — Albaraka needs the entire blob echoed back as a request
parameter.

Both banks return **`200` with all-zero values** where another API would return
an error, and in both cases some of those zeros are genuine "we do not offer
this" (Kuveyt Türk's Yuvam, Albaraka's Kur Korumalı and gold). Both verify
scripts already separate real failures from known bank-side gaps; carry that
distinction into the provider so the agent can say "this bank does not publish
that" instead of showing a zero.

## Configuration

Add to `config/settings.py`, following the existing banner style, and mirror
into `.env.example` (a unit test asserts they match):

```
BANK_HTTP_TIMEOUT: float = 30.0
BANK_HTTP_RETRIES: int = 1
BANK_USER_AGENT: str = "<a real desktop Chrome UA>"
```

Nothing else. No API keys — none of these endpoints authenticate.

## Tests

Match the existing split; `pytest.ini` already separates them.

- `tests/unit/test_banks.py` — no network. Registry resolves, capabilities are
  honest, `UnsupportedProduct` raises, response parsing maps a **recorded**
  payload onto the dataclasses, and an all-zero payload raises instead of
  returning a quote. Copy fixtures from
  `docs/discovery/captured/kuveytturk_full.json`.
- `tests/integration/test_banks_live.py` — real calls, marked `integration`.
  Assert the **contract**, never an exact number: fields present, types right,
  values in a sane range. Rates change daily and that change is not a failure.
  `verify_kuveytturk.py` already does exactly this for 53 cases; lift its
  assertions rather than inventing new ones.

## Done means

- `get_bank("kuveytturk").products("finance")` returns 19 products with codes
  and limits, from the live catalogue; `get_bank("albaraka").products("finance")`
  returns 16, parsed from the page.
- Each tool returns a correct answer for both banks, and a clear refusal for a
  bank or product that does not support it — including the known bank-side gaps,
  which must read as "not published" and never as a zero price.
- The same `finance_quote` call works against both banks with only the `bank`
  argument changed. If it does not, the interface is wrong, not the caller.
- `pytest tests/unit` passes offline; `pytest -m integration` passes online.
