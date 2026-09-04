"""create people and person embeddings tables

Revision ID: d7e8f9a0b1c2
Revises: 8f2b3c4d5e6f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "8f2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name", name="uq_people_normalized_name"),
    )
    op.create_index("ix_people_normalized_name", "people", ["normalized_name"], unique=False)
    op.create_table(
        "person_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_person_embeddings_person_id", "person_embeddings", ["person_id"], unique=False)
    op.create_index("ix_person_embeddings_model_name", "person_embeddings", ["model_name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_person_embeddings_model_name", table_name="person_embeddings")
    op.drop_index("ix_person_embeddings_person_id", table_name="person_embeddings")
    op.drop_table("person_embeddings")
    op.drop_index("ix_people_normalized_name", table_name="people")
    op.drop_table("people")
