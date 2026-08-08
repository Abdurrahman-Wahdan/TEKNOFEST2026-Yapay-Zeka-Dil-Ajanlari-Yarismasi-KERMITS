"""T.O.M. Katılım — no usable public calculator.

Like Adil, a real provider with no capabilities, but for a different reason
worth keeping: the public site is static, and the loan API that does exist
answers `401 Unauthorized: Invalid credentials` to every payload without a
partner credential.

Nothing is called. A guaranteed 401 on every user question is latency spent to
produce an error, and polling it daily would train people to ignore alerts. The
distinction from Adil matters because the remedy differs — Adil has nothing to
integrate, while T.O.M. becomes available the day someone supplies a credential.

Contract in docs/discovery/captured/tom.md.
"""

from .base import BaseBank


class Tom(BaseBank):
    name = "tom"
    display_name = "T.O.M. Katılım Bankası"
    capabilities = frozenset()
    transport = "none"
    notes = (
        "T.O.M. publishes no public calculator. Its loan API exists but "
        "requires a partner credential we do not have and answers 401 to "
        "everything, so no live figure is available today."
    )
