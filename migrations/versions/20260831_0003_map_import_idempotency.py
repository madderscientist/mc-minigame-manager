"""Add persistent idempotency for map imports.

Revision ID: 20260831_0003
Revises: 20260831_0002
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0003"
down_revision: str | None = "20260831_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("maps") as batch:
        batch.add_column(sa.Column("import_idempotency_key", sa.String(255), nullable=True))
        batch.add_column(sa.Column("import_request_hash", sa.String(64), nullable=True))
        batch.create_unique_constraint(
            "uq_maps_import_idempotency_key", ["import_idempotency_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("maps") as batch:
        batch.drop_constraint("uq_maps_import_idempotency_key", type_="unique")
        batch.drop_column("import_request_hash")
        batch.drop_column("import_idempotency_key")
