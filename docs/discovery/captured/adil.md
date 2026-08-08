# Adil Katılım — no public calculator

Checked 2026-08-08. Host **`www.adilkatilim.com.tr`** — the bank list's
`adilkatilim.com` is NXDOMAIN.

**Result: no endpoints. This is a finding, not a failure.**

## Evidence

Probed with a real browser on the correct domain:

- 0 `<input>` elements and 0 `<select>` elements on the entire site
- 0 backend XHR calls of any kind after filling and clicking every visible
  control
- no calculator links anywhere in the navigation

This was re-checked from scratch rather than trusted. The earlier "Adil has no
calculator" note had been made against `adilkatilim.com`, which does not
resolve — and a dead domain looks exactly like a bank with no calculator. The
conclusion survived the re-check on the live domain, but for a different and
now actually verified reason.

## What the tools must do

Register Adil as a **real provider with empty capabilities**, not as an absent
bank. `list_banks` should include it, and every quote tool asked about it should
answer plainly — "Adil Katılım does not publish a public calculator, so no live
figure is available" — rather than raising an unknown-bank error, timing out, or
silently returning nothing.

The distinction matters to the agent: "this bank has no such product" is a
correct, useful answer to give a user. "I could not reach the bank" is not.

## Re-checking

There is nothing to health-check here. If Adil later ships a calculator, it will
show up as inputs on the site, so a periodic re-probe belongs in discovery, not
in the daily endpoint check.
