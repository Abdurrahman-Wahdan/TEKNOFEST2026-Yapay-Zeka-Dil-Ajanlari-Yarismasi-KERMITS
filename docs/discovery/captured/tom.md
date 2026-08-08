# T.O.M. Katılım — no usable public calculator

Checked 2026-08-08. Host `www.tombank.com.tr`.

**Result: no endpoints we can call. This is a finding, not a failure.**

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
