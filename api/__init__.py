"""The HTTP boundary between the dashboard and everything this repo already does.

`banks/`, `index/`, `corpus/` and `llm/` are the system. This package adds no
banking logic of its own -- it authenticates a caller, calls those modules, and
shapes the result as JSON. If a rule about banks or campaigns is being written
here, it is in the wrong place.

    uvicorn api.main:app --reload

The layout mirrors that job:

    api/
    ├── main.py        the app: middleware, routers, lifespan
    ├── security.py    password hashing and JWT minting/verification
    ├── deps.py        the shared FastAPI dependencies (db session, current user)
    ├── db/            SQLAlchemy models and the session factory
    ├── schemas/       pydantic request/response models -- also the OpenAPI
    │                  contract the frontend's TypeScript types are generated from
    └── routers/       one module per resource
"""

__all__ = []
