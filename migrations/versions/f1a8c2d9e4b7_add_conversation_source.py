"""add indexed conversation source

Revision ID: f1a8c2d9e4b7
Revises: e6dab644bb8e
Create Date: 2026-08-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a8c2d9e4b7"
down_revision: Union[str, Sequence[str], None] = "e6dab644bb8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Store chat ownership outside JSON and index the history lookup."""
    op.add_column(
        "conversations",
        sa.Column("source", sa.String(length=16), nullable=True),
    )

    # Existing local chats were identified only by metadata.type. All other
    # historical conversations originated from Gemini Live.
    op.execute(
        """
        UPDATE conversations
        SET source = CASE
            WHEN json_valid(metadata)
                 AND json_extract(metadata, '$.type') = 'local_llm'
                THEN 'local'
            ELSE 'gemini'
        END
        """
    )

    # SQLite needs a batch rebuild to add a NOT NULL constraint and CHECK.
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.alter_column("source", existing_type=sa.String(length=16), nullable=False)
        batch_op.create_check_constraint(
            "ck_conversations_source", "source IN ('gemini', 'local')"
        )

    op.create_index(
        "ix_conversations_source_started_at",
        "conversations",
        ["source", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_source_started_at", table_name="conversations")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("ck_conversations_source", type_="check")
        batch_op.drop_column("source")
