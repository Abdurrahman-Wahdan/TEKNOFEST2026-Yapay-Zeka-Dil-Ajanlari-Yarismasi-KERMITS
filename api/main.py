"""The app.

    uvicorn api.main:app --reload --port 8000

Docs at /docs, and the OpenAPI schema at /openapi.json -- which is what
`UI/ npm run api:types` reads to generate the frontend's TypeScript types.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings

from .routers import ROUTERS

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TF26 API",
    description=(
        "Turkish participation-banking campaigns and live pricing. "
        "This service authenticates callers and exposes what `banks/`, "
        "`index/` and `corpus/` already do; it holds no banking logic itself."
    ),
    version="0.1.0",
    # Every operation gets a readable id, so the generated TypeScript client has
    # `getBankFinanceQuote()` rather than the default
    # `bank_finance_quote_api_banks__bank__finance_get()`.
    generate_unique_id_function=lambda route: route.name,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ROUTERS:
    app.include_router(router, prefix="/api")

logger.info(
    "TF26 API ready — environment=%s, CORS origins=%s",
    settings.ENVIRONMENT,
    ", ".join(settings.cors_origins),
)
