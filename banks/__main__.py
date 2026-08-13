"""`python -m banks` runs the health check.

    python -m banks                              every bank
    python -m banks --bank vakif                 one bank
    python -m banks --capability finance --json  one capability, machine-readable

`python -m banks.health` does the same thing; both land here.
"""

import sys

from .health import main

if __name__ == "__main__":
    sys.exit(main())
