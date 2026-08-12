"""Liveness, readiness, and what the nightly jobs last did.

Two different questions, deliberately two endpoints:

    /health   is this process running?      -- never touches a dependency
    /ready    can it actually serve?        -- checks Postgres and Qdrant

Collapsing them is the classic mistake: a liveness probe that checks the
database restarts a perfectly healthy API every time the database blinks, which
turns a brief outage into an outage plus a cold start.
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from config.settings import settings
from vector_stores.client import get_qdrant_client

from ..db.session import engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


class HealthOut(BaseModel):
    status: str = "ok"
    environment: str


class DependencyOut(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class ReadyOut(BaseModel):
    ready: bool = Field(description="False if any dependency is down.")
    dependencies: list[DependencyOut]


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness. Answers as long as the process is up."""
    return HealthOut(environment=settings.ENVIRONMENT)


@router.get("/ready", response_model=ReadyOut)
def ready() -> ReadyOut:
    """Readiness: every dependency checked, and every failure named.

    All dependencies are probed even after the first failure. A report saying
    only "Postgres is down" when Qdrant is down too costs a second deploy to
    discover the second problem.
    """
    checks: list[DependencyOut] = []

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks.append(DependencyOut(name="postgres", ok=True))
    except Exception as exc:
        logger.warning("Postgres not ready: %s", exc)
        checks.append(DependencyOut(name="postgres", ok=False, detail=str(exc)[:200]))

    try:
        get_qdrant_client().get_collections()
        checks.append(DependencyOut(name="qdrant", ok=True))
    except Exception as exc:
        logger.warning("Qdrant not ready: %s", exc)
        checks.append(DependencyOut(name="qdrant", ok=False, detail=str(exc)[:200]))

    return ReadyOut(ready=all(c.ok for c in checks), dependencies=checks)
