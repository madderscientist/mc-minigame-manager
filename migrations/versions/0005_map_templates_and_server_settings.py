"""Add map template source and managed server settings.

Revision ID: 20260904_0005
Revises: 20260904_0004
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0005"
down_revision: str | None = "20260904_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _recover_interrupted_batch(table_name: str) -> set[str]:
    temporary_name = f"_alembic_tmp_{table_name}"
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if table_name in tables and temporary_name in tables:
        op.drop_table(temporary_name)
    elif table_name not in tables and temporary_name in tables:
        op.rename_table(temporary_name, table_name)
    elif table_name not in tables:
        raise RuntimeError(f"Migration requires the {table_name} table")
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def upgrade() -> None:
    map_columns = _recover_interrupted_batch("maps")
    if "source_type" not in map_columns:
        op.add_column(
            "maps",
            sa.Column(
                "source_type",
                sa.Enum(
                    "uploaded",
                    "generated",
                    name="map_source_type",
                    native_enum=False,
                ),
                nullable=False,
                server_default="uploaded",
            ),
        )
    if "server_settings" not in map_columns:
        op.add_column(
            "maps",
            sa.Column(
                "server_settings",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    game_columns = _recover_interrupted_batch("games")
    if "server_settings" not in game_columns:
        op.add_column(
            "games",
            sa.Column(
                "server_settings",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.drop_column("server_settings")
    with op.batch_alter_table("maps") as batch:
        batch.drop_column("server_settings")
        batch.drop_column("source_type")