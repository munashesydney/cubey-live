"""create maps table for 2D house floor plans

Revision ID: 8f2b3c4d5e6f
Revises: f1a8c2d9e4b7
Create Date: 2026-08-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8f2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "f1a8c2d9e4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("resolution_cm", sa.Float(), nullable=False, server_default="5.0"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="400"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="400"),
        sa.Column("origin_x_m", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("origin_y_m", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("grid_data", sa.LargeBinary(), nullable=False),
        sa.Column("waypoints_json", sa.Text(), nullable=True, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_maps_name", "maps", ["name"], unique=False)
    op.create_index("ix_maps_is_active", "maps", ["is_active"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_maps_is_active", table_name="maps")
    op.drop_index("ix_maps_name", table_name="maps")
    op.drop_table("maps")
