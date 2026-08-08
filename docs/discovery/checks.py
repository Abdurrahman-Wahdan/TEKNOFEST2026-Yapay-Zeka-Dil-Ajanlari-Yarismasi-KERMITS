"""Shared plumbing for the per-bank verify scripts.

Every bank gets its own verify_<bank>.py because every bank's contract is
different, but the reporting is the same everywhere: assert the contract, never
an exact number, and keep the bank's own gaps separate from our failures.

verify_kuveytturk.py and verify_albaraka.py predate this module and carry their
own copies; they pass, so they were left alone rather than churned.
"""

import re
import sys


class Report:
    """Collects pass/fail plus the bank's own gaps, and exits accordingly."""

    def __init__(self, bank):
        self.bank = bank
        self.results = []
        self.gaps = []

    def check(self, name, ok, detail=""):
        self.results.append((name, bool(ok)))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""),
              flush=True)
        return ok

    def known(self, name, detail):
        """A gap on the bank's side, confirmed against their own page. Not a
        pass and not our failure -- visible, but it does not fail the run."""
        self.gaps.append((name, detail))
        print(f"  KNOWN {name}   {detail}", flush=True)

    def section(self, title):
        print(f"\n== {title}", flush=True)

    def finish(self):
        failed = [n for n, ok in self.results if not ok]
        print(f"\n== {self.bank}: {len(self.results) - len(failed)}/{len(self.results)} "
              f"passed, {len(self.gaps)} known bank-side gap(s)")
        for n in failed:
            print(f"   FAILED: {n.strip()}")
        for n, d in self.gaps:
            print(f"   KNOWN:  {n.strip()} — {d}")
        sys.exit(1 if failed else 0)


def amount(text):
    """'6.684,28 TL' -> 6684.28.

    Turkish formatting, and the currency suffix varies by product, so comparing
    the string against '0,00 TL' silently accepts '0,00 USD' as a real value.
    Parse, then compare numerically.
    """
    if text is None:
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = re.sub(r"[^\d,.-]", "", str(text))
    if not cleaned:
        return 0.0
    # Turkish: dot groups thousands, comma is the decimal separator.
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

AJAX = {
    "user-agent": UA,
    "x-requested-with": "XMLHttpRequest",
    "accept": "application/json, text/javascript, */*; q=0.01",
}
