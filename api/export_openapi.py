"""Write the OpenAPI schema to a file. The first half of `npm run api:types`.

    python -m api.export_openapi UI/openapi.json

Dumping to a file rather than curling a running server on purpose: generating
the frontend's types must not depend on the API being up, or a fresh clone
cannot typecheck until someone starts uvicorn.
"""

import json
import sys
from pathlib import Path

from .main import app

DEFAULT_OUT = Path(__file__).parent.parent / "UI" / "openapi.json"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0]) if argv else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=False keeps Turkish characters readable in the schema, which
    # matters because the descriptions are read by whoever writes the frontend.
    out.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
