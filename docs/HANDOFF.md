# Handoff — TF26

State as of 2026-08-08. Read `FINDINGS.md` alongside this; it holds the measured
behaviour of the models and the first bank endpoint contract in full.

---

## 1. Where the project is

The repo was rebuilt from scratch this session. Everything before it was
exploratory and has been deleted on purpose.

**Committed and working** (`d8721a7`):

```
config/settings.py      one flat pydantic-settings class, roles validated at startup
llm/                    factory + providers/{base,vllm_provider}   3 local models
embeddings/             factory + providers/{base,local_provider}  sentence-transformers
vector_stores/          factory + client + providers/{base,qdrant_provider}
tests/unit/ (24)        no network
tests/integration/ (8)  real vLLM + real Qdrant
docs/FINDINGS.md        measured model behaviour, vLLM flags, MCP traps, endpoints
docs/ARCHITECTURE.md    layout and how to add a provider
```

`32 passed, 4 skipped` — the skips are embedding tests that refuse to trigger a
2 GB model download. Warm the cache with
`python -c "from embeddings import get_embedding; get_embedding()"`.

Run everything with `~/.pyenv/versions/tf26/bin/python`. Qdrant must be up for
integration tests:
`docker run -d --name qdrant -p 6333:6333 -v "$HOME/qdrant_storage:/qdrant/storage" qdrant/qdrant`

**Not built yet:** the agent layer, the bank registry, the health checker. The
skills system was deliberately abandoned — see §5.

---

## 2. What the user actually wants (the target architecture)

Three phases, agreed explicitly:

1. **Discovery, once per bank.** An agent drives each bank's calculator in a
   browser, watches the network, and records the backend endpoint contract.
   Dispatched by hand.
2. **Live use.** The chatbot calls those endpoints directly at question time
   with the user's own amount and term. No browser. ~0.3 s instead of ~130 s.
3. **Daily health check, no AI.** Plain software calls every recorded endpoint
   each morning. All green → the agent never runs. Bank X fails → re-run
   discovery for that bank only.

Rules the user set, in their words:

- **The agent must adapt, not memorise.** A new bank with an unfamiliar layout
  has to be findable by reasoning. It must also be able to conclude "this bank
  has no calculator" and record that as a legitimate answer.
- **Never compute a number ourselves.** The endpoint's value is the truth. No
  reverse-engineered formulas. (One was derived for Kuveyt Türk — it is in
  FINDINGS as a curiosity, explicitly *not* a fallback.)
- **All products**, whatever each bank calculates live.
- **Everything in English** — code, comments, docs.
- **Write only what we use.** No stub providers, no unused settings fields, no
  API-key placeholders until an API provider actually exists.
- Short and plain. Over-engineering is unacceptable.
- Endpoint records live in the repo for now.

---

## 3. Bank discovery — where it got to

Ten banks (`example.bank.list.txt`). **9 of 10 have live calculators.**

| bank | platform | endpoint(s) confirmed |
|---|---|---|
| Kuveyt Türk | Magiclick | `POST /ck0d84?3013…` finansman · `POST /ck0d84?1E32…` kâr payı |
| Albaraka | Unigate | `GET /plugins/getFinanceCalculate?Slug=…` · `GET /plugins/getProfitShareCalculate` |
| Vakıf | Unigate | `POST /plugins/FinancingComputationExecute` · `InstallmentPayBack` · `FinancingInstallment` · `GrossAmountCalculationJson` |
| Emlak | Unigate | `GET /Plugins/CalculateProfitShareRate` · `GET /Plugins/CalculateLoansProduct` |
| Ziraat | Drupal | `POST /ajax/finansmanhesapla?_wrapper_format=drupal_ajax` · `POST /ajax/get-vade` |
| Türkiye Finans | SharePoint | `GET /_vti_bin/TurkiyeFinansServices/FrontEndService.svc/GetFinanceCalculatorCreditTypeItems` |
| T.O.M. | REST | `POST webintegration.tombank.com.tr/webintegration/api/LoanCalculation/GetLoanPayBackPlan` |
| Dünya | ASP.NET Core | `POST /LoanCheckRate?lang=tr` · `POST /DividendEstimatedProfit?lang=tr` |
| Hayat Finans | Next.js | `POST /api/integration/calculateloansproduct` |
| **Adil** | — | **none.** 17 links total, 10 internal, zero form fields anywhere |

Raw captures with request/response bodies: `docs/discovery/captured/*.json`.
Only the first Kuveyt Türk contract (alışveriş finansmanı) is fully verified
end to end — see FINDINGS §9 for its exact body, headers and a cost comparison.

### The generalisation that makes the agent adaptable

Fingerprint the CMS, then look for **that platform's XHR convention**:

| platform | convention |
|---|---|
| Unigate | `/[Pp]lugins/<Name>` |
| Magiclick | `/ck0d84?<hash>` — opaque, **a different hash per calculator** |
| Drupal | `/ajax/<name>?_wrapper_format=drupal_ajax` |
| SharePoint | `/_vti_bin/<Service>.svc/<Method>` |
| ASP.NET Core | `/<Action>?lang=tr` + CSRF token |
| Next.js / REST | `/api/...` JSON |

I predicted the Unigate shape after Albaraka and it held for Vakıf and Emlak —
three for three on plugin names never seen before. That is the rule to encode,
not a list of paths.

### What is still missing

- Roughly **11 of an estimated 35–40 endpoints** captured. Each bank has several
  calculators (finansman variants, kâr payı, kart taksit, leasing, döviz).
- Only Kuveyt Türk's alışveriş endpoint has been called successfully **without a
  browser** and matched against the site. **Every other endpoint is unverified.**
- Product-code dimensions are not enumerated. Kuveyt Türk's single endpoint
  serves many products via `p4`/`p5`; Albaraka splits by `Slug`. Without the
  code list the chatbot can only answer for one product per bank.
- Auth requirements not mapped per endpoint. At least Vakıf, Dünya and Albaraka
  need a CSRF token or session cookie — a stateless check will 403.

### The user's last instruction (do this next)

> "get all the endpoints from one bank at a time and then see what endpoints we
> need — the necessary ones for interactions and live use — get these endpoints,
> test them, make sure it is working, then move to the next bank"

and

> "don't use the vocabulary, it will cause us to lose banks — get all the
> endpoints then see what we need"

So: **per bank, capture everything, then select, then verify over plain HTTP
before moving on.** Do not filter by Turkish banking words during capture.

---

## 4. Traps — every one of these cost real time

**Discovery**

- **A failed trigger looks exactly like "no calculator."** I wrongly reported
  four banks as having none. Each time my click had simply missed. A negative
  result is only valid once the trigger provably fired — check whether the
  on-page result changed before concluding anything.
- **The accessibility tree lies by omission.** Vakıf's inputs have ids but no
  labels, so `browser_snapshot` reported zero textboxes on a working
  calculator. TOM and Türkiye Finans buttons are not `button` nodes either.
  **Query the DOM; treat the a11y tree as a hint only.**
- **Three of nine calculators are on the homepage with no dedicated URL**
  (Ziraat, Dünya, Hayat Finans). URL-based discovery is structurally blind to
  them.
- **Vocabulary filtering loses banks.** These are participation banks: kâr payı
  not faiz, finansman not kredi, katılma hesabı not mevduat. Worse, my exclusion
  regex `/ara|search/` silently matched **`anapara`** and skipped every
  principal-amount field.
- **Clicking anchors navigates away** and destroys page state. Skip `<a>` with a
  real `href`.
- **Setting `.value` is not enough** — use the native setter plus
  `input`/`change`/`keyup`/`blur`, or framework listeners never fire.
- **Pages fire a call on load with their own defaults.** Albaraka's first
  request used 150000/23, not my input. Naive diffing captures the wrong one.
- **Don't judge a request by its URL.** `ck0d84?<hash>` looks like a bot beacon;
  it is the calculator. Only the response body tells you.
- **4 of 10 URLs in the bank list are wrong or stale.** Resolve domains first.
- **`httpx` fails where Chrome succeeds** — Dünya times out under httpx, loads
  fine in the browser. An HTTP-only health check would call it dead.
- **Cross-subdomain APIs exist.** TOM's endpoint is on
  `webintegration.tombank.com.tr`. A same-origin filter drops it — my
  `capture.py` still has this bug at line ~150 (`if origin not in ...`). Fix
  before reusing.

**Infrastructure** (details in FINDINGS §5–7)

- MCP: use `async with client.session(...)` + `load_mcp_tools(session)`.
  `get_tools()` opens a new session per call, so Playwright gets a fresh browser
  each time and the page is always `about:blank`.
- MCP tool results are **lists**; `str()` produces a Python repr with literal
  `\n`. Convert properly or regexes silently fail.
- vLLM: "System message must be at the beginning", and `AIMessage.tool_calls`
  must keep their matching `ToolMessage`s or you get a 400.
- Token estimate for Turkish + YAML is `chars/3`, not `/4`.

---

## 5. Decisions already made — don't relitigate

- **Skills are dropped.** A full three-agent skill system was built and then
  abandoned; the endpoint approach replaced it (15× faster, 50× fewer tokens).
  Only the *lesson* survives in FINDINGS §8.
- **`function_calling` for structured extraction**, never `json_schema` — the
  latter fabricates values for absent fields (qwen invented "Ziraat Bankası").
- **Thinking is disabled** on qwen at the provider level; measured 433 → 36
  output tokens with no quality loss.
- **Local only.** Three vLLM models, local embeddings, local Qdrant. Provider
  structure exists so Gemini/OpenAI is one file plus one list entry — but no
  stubs until actually needed.
- **Health check must be software, not AI**, and must validate the *contract*
  (fields present, types, sane ranges) rather than exact values, because rates
  legitimately change and that change is not an error.

---

## 6. Suggested next steps

1. Fix the same-origin bug in `docs/discovery/capture.py`, then run it per bank.
2. For Kuveyt Türk: capture all six calculators, pick the ones needed for live
   use, and **call each with plain `httpx`** — no browser — confirming the
   numbers match the site. Only then move to Albaraka.
3. Once two or three banks are verified, design the registry schema from real
   data rather than guesses: bank, product, method (`endpoint` / `browser`),
   URL, request template, response field mapping, auth requirement, product codes.
4. Then the health checker, then the discovery agent.

`docs/discovery/` holds the working probes: `capture.py` (no vocabulary
filtering), `crawl.py` (find calculators across a site), `deep.py` (single page,
verbose), plus `recon.py` / `sitemaps.py` for cheap HTTP-only reconnaissance.
