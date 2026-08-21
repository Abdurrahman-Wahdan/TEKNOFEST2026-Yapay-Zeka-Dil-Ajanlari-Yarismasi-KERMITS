"""One module per resource. `main.py` mounts them all under /api.

    auth            signup, login, refresh, me
    profile         the onboarding result and the user's saved dashboard views
    banks           the registry, one bank's products, one bank's quote
    compare         the same question at every bank
    components      a topic page's RAG content, produced by the agent
    compare_tables  the offline cross-bank comparison-table pool (dataprep.compare)
    search          the corpus index
    chat            the agent, streamed over SSE
    models          the chat models the composer lets the user pick between
    system          liveness, readiness, and the nightly jobs' last state

`banks` and `compare` serve the live endpoints; `components` and `compare_tables`
serve what a model read/synthesized out of the corpus, offline. Nothing crosses:
a live figure never arrives as a component or a comparison-table row.
"""

from . import (
    auth, banks, chat, compare, compare_tables, components, models, profile, search,
    system,
)

ROUTERS = (
    auth.router,
    profile.router,
    banks.router,
    compare.router,
    components.router,
    compare_tables.router,
    search.router,
    chat.router,
    models.router,
    system.router,
)

__all__ = ["ROUTERS"]
