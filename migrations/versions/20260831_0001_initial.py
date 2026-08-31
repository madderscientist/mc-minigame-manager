"""Initial control-plane schema.

Revision ID: 20260831_0001
Revises: None
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

map_kind = sa.Enum("repository", "active", name="map_kind", native_enum=False)
map_state = sa.Enum("preparing", "ready", "failed", name="map_state", native_enum=False)
desired_state = sa.Enum("running", "stopped", name="desired_state", native_enum=False)
observed_state = sa.Enum(
    "preparing",
    "starting",
    "ready",
    "stopping",
    "backing_up",
    "stopped",
    "failed",
    "unknown",
    name="observed_state",
    native_enum=False,
)
port_state = sa.Enum(
    "free", "reserved", "active", "releasing", name="port_state", native_enum=False
)
operation_type = sa.Enum(
    "start", "stop", "load_backup", name="operation_type", native_enum=False
)
operation_status = sa.Enum(
    "pending",
    "running",
    "succeeded",
    "failed",
    "canceled",
    name="operation_status",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "maps",
        sa.Column("map_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", map_kind, nullable=False),
        sa.Column("state", map_state, nullable=False),
        sa.Column("source_map_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("mc_version", sa.String(length=32), nullable=False),
        sa.Column("data_version", sa.Integer(), nullable=True),
        sa.Column("paper_build", sa.String(length=64), nullable=False),
        sa.Column("java_major", sa.Integer(), nullable=False),
        sa.Column("paper_url", sa.Text(), nullable=True),
        sa.Column("paper_sha256", sa.String(length=64), nullable=True),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=False),
        sa.Column("operation_lock_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(kind = 'repository' AND source_map_id IS NULL) OR "
            "(kind = 'active' AND source_map_id IS NOT NULL)",
            name="ck_map_kind_source",
        ),
        sa.ForeignKeyConstraint(["source_map_id"], ["maps.map_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("map_id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index("ix_maps_source_map_id", "maps", ["source_map_id"])
    op.create_index("ix_maps_operation_lock_id", "maps", ["operation_lock_id"])

    op.create_table(
        "backups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("active_map_id", sa.Integer(), nullable=False),
        sa.Column("backup_id", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("clean_shutdown", sa.Boolean(), nullable=False),
        sa.Column("retained", sa.Boolean(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["active_map_id"], ["maps.map_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_map_id", "backup_id", name="uq_backup_active_time"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index("ix_backups_active_map_id", "backups", ["active_map_id"])
    op.create_index("ix_backups_retained", "backups", ["retained"])

    op.create_table(
        "runtime_instances",
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("map_id", sa.Integer(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("desired_state", desired_state, nullable=False),
        sa.Column("observed_state", observed_state, nullable=False),
        sa.Column("container_name", sa.String(length=128), nullable=False),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["map_id"], ["maps.map_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("instance_id"),
        sa.UniqueConstraint("container_name"),
    )
    op.create_index("ix_runtime_instances_map_id", "runtime_instances", ["map_id"])
    op.create_index("ix_runtime_instances_port", "runtime_instances", ["port"])
    op.create_index(
        "uq_one_live_instance_per_active",
        "runtime_instances",
        ["map_id"],
        unique=True,
        sqlite_where=sa.text(
            "observed_state IN ('preparing','starting','ready','stopping','backing_up','unknown')"
        ),
    )

    op.create_table(
        "port_leases",
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("state", port_state, nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["runtime_instances.instance_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("port"),
        sa.UniqueConstraint("instance_id"),
    )

    op.create_table(
        "operations",
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("type", operation_type, nullable=False),
        sa.Column("status", operation_status, nullable=False),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("requested_map_id", sa.Integer(), nullable=True),
        sa.Column("target_map_id", sa.Integer(), nullable=True),
        sa.Column("backup_id", sa.String(length=32), nullable=True),
        sa.Column("requested_port", sa.Integer(), nullable=True),
        sa.Column("instance_id", sa.String(length=36), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["runtime_instances.instance_id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["target_map_id"], ["maps.map_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_operations_instance_id", "operations", ["instance_id"])
    op.create_index("ix_operations_status", "operations", ["status"])
    op.create_index("ix_operations_target_map_id", "operations", ["target_map_id"])
    op.create_index(
        "uq_one_pending_stop_per_instance",
        "operations",
        ["instance_id"],
        unique=True,
        sqlite_where=sa.text("type = 'stop' AND status IN ('pending','running')"),
    )


def downgrade() -> None:
    op.drop_table("operations")
    op.drop_table("port_leases")
    op.drop_index("uq_one_live_instance_per_active", table_name="runtime_instances")
    op.drop_table("runtime_instances")
    op.drop_table("backups")
    op.drop_table("maps")
