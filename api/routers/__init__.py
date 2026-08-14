"""One module per resource. `main.py` mounts them all under /api.

    auth        signup, login, refresh, me
    profile     the onboarding result and the user's saved dashboard views
    banks       the registry, one bank's products, one bank's quote
    compare     the same question at every bank
    components  a topic page's RAG content, produced by the agent
    search      the corpus index
    chat        the agent, streamed over SSE
    system      liveness, readiness, and the nightly jobs' last state

`banks` and `compare` serve the live endpoints; `components` serves what a model
read out of the corpus. Nothing crosses: a live figure never arrives as a
component, and a corpus table never claims to be a quote.
"""

from . import auth, banks, chat, compare, components, profile, search, system

ROUTERS = (
    auth.router,
    profile.router,
    banks.router,
    compare.router,
    components.router,
    search.router,
    chat.router,
    system.router,
)

__all__ = ["ROUTERS"]
