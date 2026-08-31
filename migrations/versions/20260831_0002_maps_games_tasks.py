"""Split repository maps from persistent games and make runs internal.

Revision ID: 20260831_0002
Revises: 20260831_0001
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0002"
down_revision: str | None = "20260831_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

resource_state = sa.Enum(
    "preparing", "ready", "failed", name="resource_state", native_enum=False
)
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
task_type = sa.Enum(
    "create_game",
    "delete_game",
    "start",
    "stop",
    "load_backup",
    name="task_type",
    native_enum=False,
)
task_status = sa.Enum(
    "pending",
    "running",
    "succeeded",
    "failed",
    "canceled",
    name="task_status",
    native_enum=False,
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")

    op.create_table(
        "maps_v2",
        sa.Column("map_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("state", resource_state, nullable=False),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("map_id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_table(
        "games",
        sa.Column("game_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("map_id", sa.Integer(), nullable=False),
        sa.Column("state", resource_state, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("task_lock_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["map_id"], ["maps_v2.map_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("game_id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_table(
        "backups_v2",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("backup_id", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("clean_shutdown", sa.Boolean(), nullable=False),
        sa.Column("retained", sa.Boolean(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "backup_id", name="uq_backup_game_time"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("container_name"),
    )
    op.create_table(
        "port_leases_v2",
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("state", port_state, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("port"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("type", task_type, nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("map_id", sa.Integer(), nullable=True),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("backup_id", sa.String(length=32), nullable=True),
        sa.Column("requested_port", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
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
        sa.ForeignKeyConstraint(["map_id"], ["maps_v2.map_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["game_id"], ["games.game_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint("idempotency_key"),
    )

    connection.exec_driver_sql(
        """
        INSERT INTO maps_v2 (
            map_id, state, name, mc_version, data_version, paper_build, java_major,
            paper_url, paper_sha256, relative_path, content_sha256, extra_metadata,
            created_at, updated_at
        )
        SELECT map_id, state, name, mc_version, data_version, paper_build, java_major,
               paper_url, paper_sha256, relative_path, content_sha256, extra_metadata,
               created_at, updated_at
        FROM maps WHERE kind = 'repository'
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO games (
            game_id, map_id, state, name, relative_path, content_sha256,
            task_lock_id, created_at, updated_at, last_played_at
        )
        SELECT map_id, source_map_id, state, name, relative_path, content_sha256,
               operation_lock_id, created_at, updated_at, last_played_at
        FROM maps WHERE kind = 'active'
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO backups_v2 (
            id, game_id, backup_id, relative_path, reason, clean_shutdown,
            retained, size_bytes, sha256, created_at
        )
        SELECT id, active_map_id, backup_id, relative_path, reason, clean_shutdown,
               retained, size_bytes, sha256, created_at
        FROM backups
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO runs (
            run_id, game_id, port, desired_state, observed_state, container_name,
            container_id, generation, row_version, last_error, created_at, updated_at,
            ready_at, stopped_at
        )
        SELECT instance_id, map_id, port, desired_state, observed_state, container_name,
               container_id, generation, row_version, last_error, created_at, updated_at,
               ready_at, stopped_at
        FROM runtime_instances
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO port_leases_v2 (
            port, state, run_id, generation, reserved_at, updated_at
        )
        SELECT port, state, instance_id, generation, reserved_at, updated_at
        FROM port_leases
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO tasks (
            task_id, type, status, step, idempotency_key, request_hash,
            map_id, game_id, backup_id, requested_port, run_id, claimed_by,
            lease_expires_at, attempt_count, next_attempt_at, error_code,
            error_message, result, progress, row_version, created_at, updated_at,
            finished_at
        )
        SELECT o.operation_id, o.type, o.status, o.step, o.idempotency_key, o.request_hash,
               COALESCE(g.map_id, r.map_id), o.target_map_id, o.backup_id,
               o.requested_port, o.instance_id, o.claimed_by, o.lease_expires_at,
               o.attempt_count, o.next_attempt_at, o.error_code, o.error_message,
               o.result, o.progress, o.row_version, o.created_at, o.updated_at,
               o.finished_at
        FROM operations AS o
        LEFT JOIN games AS g ON g.game_id = o.target_map_id
        LEFT JOIN maps_v2 AS r ON r.map_id = o.requested_map_id
        """
    )

    op.drop_table("operations")
    op.drop_table("port_leases")
    op.drop_table("runtime_instances")
    op.drop_table("backups")
    op.drop_table("maps")
    op.rename_table("maps_v2", "maps")
    op.rename_table("backups_v2", "backups")
    op.rename_table("port_leases_v2", "port_leases")

    op.create_index("ix_games_map_id", "games", ["map_id"])
    op.create_index("ix_games_task_lock_id", "games", ["task_lock_id"])
    op.create_index("ix_backups_game_id", "backups", ["game_id"])
    op.create_index("ix_backups_retained", "backups", ["retained"])
    op.create_index("ix_runs_game_id", "runs", ["game_id"])
    op.create_index("ix_runs_port", "runs", ["port"])
    op.create_index(
        "uq_one_live_run_per_game",
        "runs",
        ["game_id"],
        unique=True,
        sqlite_where=sa.text(
            "observed_state IN ('preparing','starting','ready','stopping','backing_up','unknown')"
        ),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_map_id", "tasks", ["map_id"])
    op.create_index("ix_tasks_game_id", "tasks", ["game_id"])
    op.create_index("ix_tasks_run_id", "tasks", ["run_id"])
    op.create_index(
        "uq_one_pending_stop_per_run",
        "tasks",
        ["run_id"],
        unique=True,
        sqlite_where=sa.text("type = 'stop' AND status IN ('pending','running')"),
    )

    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Foreign-key violations after map/game migration: {violations}")
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    raise RuntimeError("The map/game identity split is intentionally irreversible")
