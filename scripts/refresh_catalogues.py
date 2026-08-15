"""Recapture every bank's product names into the coverage fixture.

    python scripts/refresh_catalogues.py

tests/unit/test_families_coverage.py reads the result to check that no product
two banks sell is missing from banks/families.py. It is a checked-in snapshot
rather than a live call so the check runs offline and fails in CI, but a
snapshot only stays useful if it is refreshed when a bank changes its range.

Run it after a bank adds or renames a product; the test will then say which
family the new product belongs to. Nothing here edits families.py -- the codes
it holds are what the endpoints take, and a code guessed from a name is a
request that 404s.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from banks import get_bank, list_banks  # noqa: E402
from banks.providers import UnsupportedProduct  # noqa: E402

TARGET = ROOT / "tests/fixtures/banks/catalogues.json"
# `card` is here for the addressability check: both duplicate-code
# collisions in the system live in Kuveyt Türk's card catalogue.
CATEGORIES = ("finance", "profit_share", "card")


def _names(bank: str) -> tuple[str, dict[str, list[str]]]:
    found: dict[str, list[str]] = {}
    for category in CATEGORIES:
        try:
            # Code and name both: a product has to stay addressable by at
            # least one of them, and only the pair can prove it.
            found[category] = [
                {"code": p.code, "name": p.name}
                for p in get_bank(bank).products(category)
            ]
        except UnsupportedProduct:
            # A bank that does not publish this catalogue is not an error; it
            # simply contributes nothing to the coverage check.
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  {bank}/{category}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return bank, found


def main() -> int:
    banks = sorted(list_banks())
    with ThreadPoolExecutor(max_workers=len(banks)) as pool:
        captured = dict(pool.map(_names, banks))

    out: dict[str, dict[str, list[str]]] = {c: {} for c in CATEGORIES}
    for bank in banks:
        for category, names in captured[bank].items():
            out[category][bank] = names

    # A bank failing outright would silently shrink the fixture and turn the
    # coverage test green for the wrong reason.
    empty = [c for c, per_bank in out.items() if not per_bank]
    if empty:
        print(f"refusing to write: no bank answered for {', '.join(empty)}", file=sys.stderr)
        return 1

    TARGET.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    for category, per_bank in out.items():
        total = sum(len(n) for n in per_bank.values())
        print(f"{category}: {total} products across {len(per_bank)} banks")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
