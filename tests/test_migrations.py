from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from mc_manager.config import Settings, get_settings
from mc_manager.db import Database


def test_initial_migration_creates_control_plane_schema(
    monkeypatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "migrated.db"
    monkeypatch.setenv("MC_DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "alembic_version",
        "maps",
        "games",
        "backups",
        "runs",
        "port_leases",
        "tasks",
    } <= tables
    assert "runtime_instances" not in tables
    assert "operations" not in tables
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "20260831_0002"
    get_settings.cache_clear()


def test_nonempty_v1_schema_migrates_to_maps_and_games(monkeypatch, tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    monkeypatch.setenv("MC_DATABASE_URL", f"sqlite:///{database_path}")
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "20260831_0001")
    engine = create_engine(f"sqlite:///{database_path}")
    now = "2026-08-31 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO maps (
                    map_id, kind, state, source_map_id, name, mc_version, data_version,
                    paper_build, java_major, relative_path, extra_metadata,
                    created_at, updated_at
                ) VALUES
                    (1, 'repository', 'ready', NULL, 'Source', '1.20.4', 3700,
                     '497', 17, 'repository/1', '{}', :now, :now),
                    (2, 'active', 'ready', 1, 'Game', '1.20.4', 3700,
                     '497', 17, 'active/2', '{}', :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO runtime_instances (
                    instance_id, map_id, port, desired_state, observed_state,
                    container_name, generation, row_version, created_at, updated_at
                ) VALUES ('run-old', 2, 30000, 'running', 'ready', 'mc-run-old',
                          1, 1, :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO backups (
                    id, active_map_id, backup_id, relative_path, reason,
                    clean_shutdown, retained, size_bytes, sha256, created_at
                ) VALUES (1, 2, '20260831T000000000000Z', 'backups/2/old',
                          'normal_stop', 1, 1, 1, :sha, :now)
                """
            ),
            {"sha": "a" * 64, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO port_leases (
                    port, state, instance_id, generation, reserved_at, updated_at
                ) VALUES (30000, 'active', 'run-old', 1, :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO operations (
                    operation_id, type, status, step, requested_map_id,
                    target_map_id, requested_port, instance_id, attempt_count,
                    next_attempt_at, progress, row_version, created_at, updated_at
                ) VALUES ('task-old', 'start', 'succeeded', 'completed', 1, 2,
                          30000, 'run-old', 1, :now, 1.0, 1, :now, :now)
                """
            ),
            {"now": now},
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT map_id, name FROM maps")).all() == [
            (1, "Source")
        ]
        assert connection.execute(
            text("SELECT game_id, map_id, name FROM games")
        ).all() == [(2, 1, "Game")]
        assert connection.execute(text("SELECT run_id, game_id FROM runs")).all() == [
            ("run-old", 2)
        ]
        assert connection.execute(
            text("SELECT task_id, map_id, game_id, run_id FROM tasks")
        ).all() == [("task-old", 1, 2, "run-old")]
        assert connection.scalar(text("SELECT game_id FROM backups")) == 2
        assert connection.scalar(text("SELECT run_id FROM port_leases")) == "run-old"
    get_settings.cache_clear()


def test_database_initialize_preserves_absolute_sqlite_path(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "manager.db"
    storage = tmp_path / "storage"
    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        storage_root=storage,
        port_min=32000,
        port_max=32000,
    )
    Database(settings).initialize()
    assert database_path.is_file()
