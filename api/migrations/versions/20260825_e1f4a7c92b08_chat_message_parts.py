"""chat messages keep the parts they were rendered from

Revision ID: e1f4a7c92b08
Revises: d93b8f5a1c62
Create Date: 2026-08-25 17:40:00+03:00

Conversation history moves from the browser to the account.

Until now `chat_messages` held the *model's* view of a turn: `role`, `content`,
`citations`. That is everything the agent needs to be given back, and it was
enough while the sidebar read from localStorage -- which is why the same signed-in
user saw a different history in every browser, and none at all in a new one.

Serving the sidebar from this table means the rows have to hold the *reader's*
view as well, and a turn on screen is not a string. It is a list of parts: the
question, the table or the row the user attached, the files, the citations under
the answer. `content` is a flattened rendering of that -- it has to stay exactly
as it is, because it is what `api/routers/chat.py` replays to the model -- so the
structure goes beside it rather than into it.

`parts` is therefore denormalised on purpose, and the direction of truth matters:
`content` is what the model reads, `parts` is what the browser draws, and the
server writes both from the same request so they cannot disagree.

Existing rows get `[]`. The API reads that as "an old row, before parts" and
rebuilds a single text part from `content` -- see `_parts_for`. So the 66
conversations already in this table become readable in every browser on the
next deploy, without a backfill that would have to guess which attachments a
turn from last week carried.

Nothing here holds bytes. The frontend's own contract already forbids it
(`UI/src/lib/chat/types.ts` documents why a base64 capture must never reach a
transcript), and this column inherits that: a capture contributes its label and
its dimensions, never its pixels.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e1f4a7c92b08"
down_revision: Union[str, None] = "d93b8f5a1c62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "parts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            # A server default as well as the model default: this runs against a
            # table with rows in it, and NOT NULL with no default cannot be added
            # to a populated table at all. Kept afterwards rather than dropped,
            # so a row written by anything that does not know about this column
            # still satisfies the constraint.
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "parts")
