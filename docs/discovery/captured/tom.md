# T.O.M. Katılım — no usable public calculator

Checked 2026-08-08. Host `www.tombank.com.tr`.

**Superseded 2026-08-13** — see `banks/providers/tom.py`'s module docstring.
The "partner credential" below turned out not to be partner-only: it is a
fixed HTTP Basic pair embedded in `/hesaplama-araclari.html`'s own JavaScript,
served to anyone, so the loan API is public after all and financing is
implemented. Left in place as the record of how the negative finding below was
reached, not as the current state.

**Original result: no endpoints we can call. This is a finding, not a failure.**

## Evidence

The public site is static. `/taksitle.html` and `/altin-biriktiren-hesap.html`
are campaign pages: 0 inputs, 0 selects, 0 iframes, 0 backend calls. There is no
calculator to drive and no XHR to capture.

A loan API does exist on a separate subdomain:

```
POST https://webintegration.tombank.com.tr/webintegration/api/LoanCalculation/GetLoanPayBackPlan
→ 401  Unauthorized: Invalid credentials
```

The host resolves and the route is live — it answers `401`, not `404`, so the
endpoint is real. Every payload shape tried returns the same `401`, with no
credential obtainable from the public site. `basvuru.`, `api.` and `app.`
subdomains do not resolve.

So the endpoint is **known but unusable**: it needs a partner credential we do
not have and should not attempt to obtain.

**Added 2026-08-16, now that the endpoint is called for real:**
`GetLoanPayBackPlan`'s `Data` carries `MonthlyProfitRate` (the nominal rate),
`MonthlyCostRate` (a fee-loaded monthly figure, materially higher than the
nominal rate), and `TotalCost` — which is `(1 + MonthlyCostRate/100) ** 12 - 1`
to five decimal places against a live quote (83.46148 vs 83.4614766). `TotalCost`
*is* the annual cost rate, stated by the bank, not computed here — it went
unread for a while on the mistaken belief that no such figure existed.

## What the tools must do

Same as Adil: register T.O.M. as a **real provider with empty capabilities** and
answer plainly that no live figure is available. Do not attempt the call — a
guaranteed `401` on every user question is latency spent to produce an error.

The reason differs from Adil's and is worth keeping in the record, because the
remedy differs: Adil has nothing to integrate, while T.O.M. becomes available
the day someone supplies a credential. If that happens, the contract is one
`POST` away and the parameter names are the only unknown.

## Re-checking

Nothing to health-check. Do not poll the `401` endpoint.
