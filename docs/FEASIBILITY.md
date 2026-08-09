# Can an agent onboard a new bank, and repair one that breaks?

Measured 2026-08-08 against the live banks and the live vLLM host. Written down
because the answer is not obvious in either direction, and because the expensive
part of re-deriving it is the measuring.

Two questions were asked. Can an agent (a) onboard a **new** bank from just a
URL, and (b) **self-heal** when a bank changes and breaks an endpoint — running
on the local models, not a cloud model?

---

## 1. Live use on the local models — proven, works today

`build_tools()` bound to each local model through `create_react_agent`:

> *"Vakıf Katılım'da 100.000 TL için 1 yıl vadeli kâr payı ne kadar?"*
> → `list_products`, then `profit_share_quote` with the term in months
> → 364 gün, net **31.323,29 TL**, matching the discovery figure exactly.

All three models — `qwen`, `gemma`, `gpt-oss` — answered correctly, on clear and
on deliberately ambiguous phrasings. With a bank marked down in the status file,
all three relayed the outage and **none invented a figure**.

This is the part that was uncertain and is now settled. It needs no cloud model.

## 2. Self-repair — feasible for the common drift, in pure code

The likeliest Kuveyt Türk breakage is a rotated `ck0d84?<hash>`. Tested:

- All six known hashes **are** recoverable from the page HTML — but so are
  **119 others**, identical on every calculator page. Position identifies
  nothing, exactly as `FINDINGS` §9 warns: only the response body tells you.
- Replaying the **recorded request** against candidates and asserting the
  **recorded response shape** (`Meta.InstallmentPayment > 0`) picked out the
  correct hash with no false positives. Wrong candidates fail fast — 405, 404,
  400, 500 — so the sweep is cheap.

That is deterministic repair for hash and URL drift, with no model involved. It
is the same shape as the industry pattern for self-healing scrapers (propose
candidates, test against live), with a stronger oracle: a bank contract asserts
numbers, not just that an element exists.

| failure | repairable without a model? |
|---|---|
| CSRF token rotated | already automatic (`refresh=True` retry) |
| WAF appears | one line: `transport = "impersonate"`; detectable from the rejection HTML |
| Endpoint hash or URL changed | **yes** — enumerate from the page, replay, assert |
| Catalogue regex broken | no — needs a model; fails loudly today |
| Response field renamed | no — needs a model |
| New parameter semantics | no — needs a human |

## 3. Auto-onboarding a new bank — a reviewed draft, not a ship

`FINDINGS` §9's experiment is decisive in both directions. A generic prompt
**failed and never read a single response body**. The same model, with the
method in the prompt, produced a correct contract. And ~29 of its 35 tool calls
went on fighting the browser form — which `docs/discovery/probe.py` already does
deterministically. So the model's real job is narrow: pick which captured call
is the calculator, name the parameters, establish units, classify the four
flavours of "no data".

What argues against shipping unreviewed is the per-bank judgement recorded in
`docs/discovery/captured/`: `p8` is not cosmetic; `ProductCode` is not unique at
three banks; Ziraat's ceiling *falls* as the term rises; Hayat's günlük account
ignores the term; zeros mean six different things. A generator handed a perfect
contract could write perhaps 35–40% of a provider — and the contract is the hard
part, not the code.

**So: onboarding produces a verified contract for human review.**

## 4. The spine both later phases need

Repair and onboarding are harder than they need to be because a bank's contract
lives in three places at once — prose in `docs/discovery/captured/<bank>.md`,
Python in `banks/providers/<bank>.py`, assertions in
`docs/discovery/verify_<bank>.py`. Nothing can act on it programmatically.

`banks/probes.py` is the smallest useful step towards that: one known-good call
per capability, as data. Extending it into a full recorded contract (request
plus response assertion) is what makes phase 2 mechanical.

---

## What was built off the back of this

Phase 1, in `banks/health.py`, `status.py`, `notify.py`, `probes.py`. See
`docs/BANK_TOOLS.md` §15.

## Phases 2 and 3, designed but not built

**Phase 2 — self-repair.** Extend probe specs into full recorded contracts. On a
red check: re-fetch the page, enumerate candidate endpoints, replay, assert.
Apply only if that bank's **entire** check suite then passes; otherwise write a
proposed diff and notify. Confine auto-apply to constant substitution — hash,
URL, token — never to parsing logic, because a wrong repair there produces
confident wrong money.

**Phase 3 — onboarding.** `probe.py` captures deterministically; a local-model
agent proposes a contract from the captures; the phase 1 runner verifies it; a
human reviews contract plus passing report before a provider is written. Encode
the method in the prompt — the measured experiment says that is the whole
difference between failure and a correct contract. Budget the context: one
`browser_snapshot` is ~16K tokens against a 65K window, and tokens are
`chars/3`, not `/4`.
