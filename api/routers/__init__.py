"""One module per resource. `main.py` mounts them all under /api.

    auth      signup, login, refresh, me
    profile   the onboarding result and the user's saved dashboard views
    banks     the registry, one bank's products, one bank's quote
    compare   the same question at every bank
    search    the corpus index
    chat      the agent, streamed over SSE
    system    liveness, readiness, and the nightly jobs' last state
"""

from . import auth, banks, chat, compare, profile, search, system

ROUTERS = (
    auth.router,
    profile.router,
    banks.router,
    compare.router,
    search.router,
    chat.router,
    system.router,
)

__all__ = ["ROUTERS"]
