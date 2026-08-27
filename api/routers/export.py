"""One endpoint, four formats, both sources.

`POST /api/export` rather than a `GET` with the table in the query string, for
the reason `api/routers/chat.py` already records about putting a user's question
in a URL: query strings are logged by every proxy in the path and land in the
browser's history. A comparison table is the user's own working state.

That the table travels in the *request* is what makes the scope toggle work
without the server knowing anything about filters. The browser sends either the
rows the user is looking at or the whole table, and there is no third
possibility for the two to disagree about.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Response, status

from ..db.models import AutomationReport
from ..deps import CurrentUser, DbSession
from ..export import (
    ExportEmpty,
    ExportUnavailable,
    WRITERS,
    report_document,
    table_document,
)
from ..export.filename import content_disposition
from ..schemas.export import ExportRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/export", tags=["export"])


def _own_report(session, user, report_id: uuid.UUID) -> AutomationReport:
    """This user's report, or 404.

    404 rather than 403, the rule `api/routers/automations.py::_own_report`
    already sets and for the same reason: a 403 confirms the id exists.
    """
    row = session.get(AutomationReport, report_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such report.")
    return row


@router.post(
    "",
    response_class=Response,
    responses={
        200: {
            "description": "The file.",
            "content": {
                "text/csv": {},
                "application/pdf": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {},
            },
        },
        422: {"description": "Nothing to export, or a format this source cannot take."},
        503: {"description": "The server is missing a tool this format needs."},
    },
)
def export(body: ExportRequest, user: CurrentUser, session: DbSession) -> Response:
    """Turn a table or a report into a file.

    Held in memory and returned whole rather than streamed. These are documents,
    not feeds: the PDF writer has to lay out every page before it knows what page
    two looks like, and a `Content-Length` is what lets the browser show a real
    progress bar instead of an indeterminate spinner.
    """
    if body.source.kind == "report":
        row = _own_report(session, user, body.source.report_id)
        document = report_document(
            title=row.title, body=row.body, citations=row.citations
        )
        if not document.blocks:
            # A failed run stores `status="failed"` with an empty body. Refused
            # rather than served as a title page with nothing under it.
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "This report has no content to export."
                + (f" It failed: {row.error}" if row.error else ""),
            )
    else:
        document = table_document(body.source.table)

    writer = WRITERS[body.format]
    try:
        payload = writer.write(document)
    except ExportUnavailable as error:
        # Something is missing on the machine, not wrong with the request. The
        # message names the binary and its install line; it is for whoever runs
        # the server, and the frontend shows it verbatim for that reason.
        logger.warning("export unavailable format=%s: %s", body.format, error)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, str(error)
        ) from error
    except ExportEmpty as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error

    logger.info(
        "export user=%s format=%s source=%s bytes=%d",
        user.id,
        body.format,
        body.source.kind,
        len(payload),
    )
    return Response(
        content=payload,
        media_type=writer.media_type,
        headers={
            "Content-Disposition": content_disposition(
                document.title, writer.extension
            ),
            # The filename is the only thing the browser needs from the headers
            # and it is not on the CORS safelist, so without this the download
            # silently falls back to the URL's last segment -- "export".
            "Access-Control-Expose-Headers": "Content-Disposition",
            # A snapshot of live bank data. Nothing should hold onto it.
            "Cache-Control": "no-store",
        },
    )
