"""`POST /api/export` over HTTP: who may call it, what it refuses, what it names
the file.

No database and no model. The session and the caller are overridden, so what
this pins is the endpoint's contract -- the format×source matrix, ownership, and
the `Content-Disposition` a browser reads the filename out of.
"""

import shutil
import uuid
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from api.db.models import AutomationReport
from api.db.session import get_session
from api.deps import get_current_user
from api.main import app

pytestmark = pytest.mark.unit

USER = SimpleNamespace(id=uuid.uuid4(), is_active=True)

TABLE = {
    "title": "Konut Finansmanı Karşılaştırması",
    "columns": [
        {"key": "banka", "label": "Banka", "type": "bank"},
        {"key": "oran", "label": "Kâr Oranı", "type": "percent", "align": "right"},
    ],
    "rows": [
        {
            "cells": [
                {"value": "ziraat", "display": "Ziraat Katılım"},
                {"value": 2.89, "display": "%2,89"},
            ],
            "cite_url": "https://ziraatkatilim.com.tr/konut",
        }
    ],
}


def _report(owner=USER, body="## Özet\n\nMetin.\n", **overrides):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=owner.id,
        title="Haftalık rapor",
        body=body,
        citations=[],
        status="ok",
        error="",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class _Session:
    """Answers exactly the one lookup the router makes."""

    def __init__(self, rows=()):
        self.rows = {row.id: row for row in rows}

    def get(self, model, key):
        assert model is AutomationReport
        return self.rows.get(key)


@pytest.fixture
def client():
    session = _Session()
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_session] = lambda: session
    made = TestClient(app)
    made.session = session
    yield made
    app.dependency_overrides.clear()


def _table_body(fmt="csv", table=None):
    return {"format": fmt, "source": {"kind": "table", "table": table or TABLE}}


# ----- tables -----


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "pdf", "docx"])
def test_a_table_exports_in_every_format(client, fmt):
    if fmt == "docx" and shutil.which("pandoc") is None:
        pytest.skip("pandoc is not installed")

    response = client.post("/api/export", json=_table_body(fmt))

    assert response.status_code == 200
    assert response.content


def test_the_filename_carries_the_turkish_title_and_a_timestamp(client):
    """Two exports of the same comparison a week apart are different documents.
    A Downloads folder that renames the second `(1)` has lost what told them
    apart."""
    disposition = client.post("/api/export", json=_table_body("csv")).headers[
        "content-disposition"
    ]

    assert disposition.startswith("attachment;")
    utf8_name = unquote(disposition.split("filename*=UTF-8''")[1])
    assert utf8_name.startswith("Konut Finansmanı Karşılaştırması ")
    assert utf8_name.endswith(".csv")

    # The ASCII fallback must be Latin-1 clean or the header itself would fail
    # to encode on the way out.
    ascii_name = disposition.split('filename="')[1].split('"')[0]
    ascii_name.encode("latin-1")
    assert ascii_name.startswith("konut-finansmani-karsilastirmasi-")


def test_the_browser_is_allowed_to_read_the_filename(client):
    """`Content-Disposition` is not on the CORS safelist. Without this the
    download silently falls back to the URL's last segment."""
    headers = client.post("/api/export", json=_table_body("csv")).headers

    assert "Content-Disposition" in headers["access-control-expose-headers"]


def test_an_export_is_never_cached(client):
    """A snapshot of live bank data."""
    assert client.post("/api/export", json=_table_body("csv")).headers[
        "cache-control"
    ] == "no-store"


def test_a_row_that_does_not_match_the_columns_is_refused(client):
    broken = {**TABLE, "rows": [{"cells": [{"value": "a", "display": "a"}]}]}

    assert client.post("/api/export", json=_table_body(table=broken)).status_code == 422


def test_an_unknown_format_is_refused(client):
    assert client.post("/api/export", json=_table_body("pptx")).status_code == 422


# ----- reports -----


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
def test_a_report_exports_as_a_document(client, fmt):
    if fmt == "docx" and shutil.which("pandoc") is None:
        pytest.skip("pandoc is not installed")
    row = _report()
    client.session.rows[row.id] = row

    response = client.post(
        "/api/export",
        json={"format": fmt, "source": {"kind": "report", "report_id": str(row.id)}},
    )

    assert response.status_code == 200
    assert response.content


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_a_report_refuses_the_data_formats(client, fmt):
    """A report is prose with tables in it; a CSV of prose is a file nobody can
    use. Refused with a reason rather than downloaded and discovered."""
    row = _report()
    client.session.rows[row.id] = row

    response = client.post(
        "/api/export",
        json={"format": fmt, "source": {"kind": "report", "report_id": str(row.id)}},
    )

    assert response.status_code == 422
    assert "PDF" in response.text


def test_another_users_report_is_not_found(client):
    """404 and not 403: a 403 confirms the id exists."""
    theirs = _report(owner=SimpleNamespace(id=uuid.uuid4()))
    client.session.rows[theirs.id] = theirs

    response = client.post(
        "/api/export",
        json={"format": "pdf", "source": {"kind": "report", "report_id": str(theirs.id)}},
    )

    assert response.status_code == 404


def test_a_report_that_never_arrived_is_refused_rather_than_served_empty(client):
    """A zero-content download reads as a broken button."""
    failed = _report(body="", status="failed", error="the bank timed out")
    client.session.rows[failed.id] = failed

    response = client.post(
        "/api/export",
        json={"format": "pdf", "source": {"kind": "report", "report_id": str(failed.id)}},
    )

    assert response.status_code == 422
    assert "the bank timed out" in response.text


# ----- who may call it -----


def test_export_needs_a_caller():
    app.dependency_overrides.clear()

    assert TestClient(app).post("/api/export", json=_table_body()).status_code == 401
