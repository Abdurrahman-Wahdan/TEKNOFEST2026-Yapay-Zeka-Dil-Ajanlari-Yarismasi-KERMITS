"""What the downloaded file is called, and the header that says so.

Two names go out, and browsers pick the better one they understand:

- `filename*=UTF-8''…` carries the document's real title, Turkish letters
  intact. Every current browser prefers this parameter, so a table called
  *Konut Finansmanı Karşılaştırması* lands in the Downloads folder under that
  name rather than a slug.
- `filename=` carries an ASCII fallback for anything that does not implement
  RFC 5987. The header itself must be Latin-1-encodable, so a bare Turkish name
  here would raise on the way out of Starlette rather than merely look wrong.

The timestamp is part of the name on purpose. These files are snapshots of live
bank data; two exports of the same comparison a week apart are different
documents, and a Downloads folder that silently turns the second into
`karsilastirma (1).xlsx` has lost the only thing distinguishing them.
"""

import re
from datetime import datetime
from urllib.parse import quote

from ..saved_tables import slugify
from agents.shared.clock import TZ

#: `20260827-1432` -- sortable, and unambiguous between two exports on one day.
STAMP = "%Y%m%d-%H%M"

#: Characters no filesystem should be asked to take. Everything else, including
#: Turkish letters and spaces, survives into the UTF-8 name.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

#: Long titles are real -- the comparison-table pool has some that run past 120
#: characters -- and some filesystems cap a component at 255 bytes. Turkish
#: letters are two bytes each in UTF-8, so the limit is set in characters with
#: room to spare, and only the *name* is cut. No table content is ever clipped.
_NAME_MAX = 80


def stem(title: str, moment: datetime | None = None) -> tuple[str, str]:
    """`(utf8_name, ascii_name)` for a document, without the extension."""
    stamped = (moment or datetime.now(TZ)).strftime(STAMP)

    readable = _UNSAFE.sub(" ", title).strip()
    readable = re.sub(r"\s+", " ", readable)[:_NAME_MAX].strip()

    # One slugifier for the whole application. `api/saved_tables.py::slugify`
    # already transliterates Turkish before lowercasing, and the order matters
    # there for a reason that applies here too -- a second copy would drift.
    slug = slugify(title, fallback="tf26-disa-aktarim")

    return (
        f"{readable} {stamped}" if readable else f"{slug}-{stamped}",
        f"{slug}-{stamped}",
    )


def content_disposition(title: str, extension: str, moment: datetime | None = None) -> str:
    """The `Content-Disposition` header value for an attachment."""
    utf8_name, ascii_name = stem(title, moment)
    return (
        f'attachment; filename="{ascii_name}.{extension}"; '
        f"filename*=UTF-8''{quote(f'{utf8_name}.{extension}', safe='')}"
    )
