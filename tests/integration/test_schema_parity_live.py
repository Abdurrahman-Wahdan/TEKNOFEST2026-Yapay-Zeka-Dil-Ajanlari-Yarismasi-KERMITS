"""The migrated database agrees with the models about who writes a column.

`TimestampMixin` declares `server_default=func.now()`, and that is not
decoration: because the default belongs to the *server*, SQLAlchemy leaves
`created_at` and `updated_at` out of the INSERT entirely and reads them back with
`RETURNING`. A migration that creates those columns `NOT NULL` and forgets
`server_default` therefore does not produce a row with an odd timestamp -- it
produces `NotNullViolation` on every insert, and the feature is dead on arrival.

That is exactly how `chat_feedback` shipped: every attempt to save a note
returned 500 and the table stayed empty. Nothing caught it because
`tests/unit/test_chat_feedback.py` hands `_feedback_context` a stub session, so
no test ever inserted a row -- the schema was never in the path.

So this asserts the invariant rather than the instance: *every* column whose
model declares a server default has one in the database. A future migration that
forgets one fails here instead of in front of a user.
"""

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

import api.db.models  # noqa: F401 - importing is what populates Base.metadata
from api.db.base import Base
from api.db.models import ChatFeedback, ChatMessage
from config.settings import settings

pytestmark = pytest.mark.integration


def _database_available() -> bool:
    try:
        engine = create_engine(
            settings.API_DATABASE_URL, connect_args={"connect_timeout": 3}
        )
        with engine.connect() as connection:
            connection.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001 - any failure means unavailable
        return False


requires_database = pytest.mark.skipif(
    not _database_available(),
    reason="PostgreSQL is not reachable; run `docker compose up -d postgres`",
)


@requires_database
def test_every_server_default_the_models_declare_exists_in_the_database():
    inspector = inspect(create_engine(settings.API_DATABASE_URL))
    present = set(inspector.get_table_names())

    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            continue  # A migration this database has not reached yet.
        actual = {column["name"]: column for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.server_default is None:
                continue
            if actual.get(column.name, {}).get("default") is None:
                missing.append(f"{table.name}.{column.name}")

    assert missing == [], (
        "these columns are NOT NULL with no database default, so the INSERT "
        "SQLAlchemy writes -- which omits them -- fails: " + ", ".join(missing)
    )


@requires_database
def test_a_feedback_note_can_actually_be_inserted():
    """The concrete case, which was a 500 on every single save.

    Rolled back rather than committed: this asks whether the schema accepts the
    insert, and leaving rows in a developer's database is not part of that.
    """
    engine = create_engine(settings.API_DATABASE_URL)
    with Session(engine) as db:
        message_id = db.scalar(
            select(ChatMessage.id)
            .outerjoin(ChatFeedback, ChatFeedback.message_id == ChatMessage.id)
            .where(ChatMessage.role == "assistant", ChatFeedback.id.is_(None))
            .limit(1)
        )
        if message_id is None:
            pytest.skip("no un-rated assistant message to attach a note to")

        feedback = ChatFeedback(message_id=message_id, rating="up", note="parity check")
        db.add(feedback)
        db.flush()  # the INSERT, without committing

        assert feedback.created_at is not None
        assert feedback.updated_at is not None

        db.rollback()
