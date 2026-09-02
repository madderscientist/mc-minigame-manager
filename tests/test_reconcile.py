from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from mc_manager.config import Settings
from mc_manager.db import Database
from mc_manager.enums import (
    DesiredState,
    ObservedState,
    PortState,
    ResourceState,
    TaskStatus,
    TaskType,
)
from mc_manager.models import GameRecord, MapRecord, PortLease, RunRecord, TaskRecord
from mc_manager.reconcile import Reconciler
from mc_manager.runtime.docker_backend import DockerRuntime
from mc_manager.runtime.fake import FakeRuntime


def create_map_and_game(settings: Settings) -> tuple[Database, int, int]:
    database = Database(settings)
    database.initialize()
    with database.session_factory.begin() as session:
        source = MapRecord(
            state=ResourceState.READY,
            name="Template",
            mc_version="1.20.4",
            paper_build="497",
            java_major=21,
            relative_path="pending-map",
        )
        session.add(source)
        session.flush()
        source.relative_path = f"maps/{source.map_id}"
        game = GameRecord(
            map_id=source.map_id,
            state=ResourceState.READY,
            name="Game",
            relative_path="pending-game",
        )
        session.add(game)
        session.flush()
        game.relative_path = f"games/{game.game_id}"
        return database, source.map_id, game.game_id


def test_docker_exists_rejects_exited_container(monkeypatch) -> None:
    container = SimpleNamespace(status="exited", reload=lambda: None)
    monkeypatch.setattr(DockerRuntime, "_find", lambda self, run_id: container)
    runtime = object.__new__(DockerRuntime)
    assert runtime.exists("run") is False


def test_reconcile_releases_port_when_run_is_missing(settings: Settings) -> None:
    database = Database(settings)
    database.initialize()
    with database.session_factory() as session:
        source = MapRecord(
            state=ResourceState.READY,
            name="Template",
            mc_version="1.20.4",
            paper_build="497",
            java_major=17,
            relative_path="maps/template",
        )
        session.add(source)
        session.flush()
        game = GameRecord(
            map_id=source.map_id,
            state=ResourceState.READY,
            name="Game",
            relative_path="games/reconcile",
        )
        session.add(game)
        session.flush()
        run = RunRecord(
            run_id="missing-runtime",
            game_id=game.game_id,
            port=settings.port_min,
            desired_state=DesiredState.RUNNING,
            observed_state=ObservedState.READY,
            container_name="mc-missing-runtime",
            generation=1,
        )
        session.add(run)
        session.flush()
        task = TaskRecord(
            task_id="interrupted-task",
            type=TaskType.LOAD_BACKUP,
            status=TaskStatus.RUNNING,
            game_id=game.game_id,
            run_id=run.run_id,
            lease_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        game.task_lock_id = task.task_id
        session.add(task)
        lease = session.get(PortLease, settings.port_min)
        assert lease is not None
        lease.state = PortState.ACTIVE
        lease.run_id = run.run_id
        lease.generation = 1
        session.commit()

    Reconciler(settings, database, FakeRuntime()).run()

    with database.session_factory() as session:
        run = session.get(RunRecord, "missing-runtime")
        task = session.get(TaskRecord, "interrupted-task")
        lease = session.get(PortLease, settings.port_min)
        assert run is not None and run.observed_state == ObservedState.FAILED
        assert task is not None and task.status == TaskStatus.PENDING
        game = session.get(GameRecord, run.game_id)
        assert game is not None and game.task_lock_id == task.task_id
        assert lease is not None and lease.state == PortState.FREE
        assert lease.run_id is None


def test_reconcile_recovers_interrupted_restore_directories(settings: Settings) -> None:
    database, _map_id, game_id = create_map_and_game(settings)
    destination = settings.game_root / str(game_id)
    destination.mkdir(parents=True)
    (destination / "state.txt").write_text("old", encoding="utf-8")
    rollback = settings.game_root / (
        f".restore-game-{game_id}-20260101T000000000000Z-old-"
        "0123456789abcdef0123456789abcdef.tmp"
    )
    destination.replace(rollback)
    staging = settings.game_root / (
        f".restore-game-{game_id}-20260101T000000000000Z-new-"
        "fedcba9876543210fedcba9876543210.tmp"
    )
    staging.mkdir()
    (staging / "state.txt").write_text("new", encoding="utf-8")

    Reconciler(settings, database, FakeRuntime()).run()

    assert (destination / "state.txt").read_text(encoding="utf-8") == "old"
    assert not rollback.exists()
    assert not staging.exists()

    stale_rollback = settings.game_root / (
        f".restore-game-{game_id}-20260101T000000000000Z-old-"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tmp"
    )
    stale_rollback.mkdir()
    Reconciler(settings, database, FakeRuntime()).run()
    assert not stale_rollback.exists()


def test_reconcile_recovers_or_collects_map_delete_trash(settings: Settings) -> None:
    database, map_id, _game_id = create_map_and_game(settings)
    destination = settings.map_root / str(map_id)
    destination.mkdir(parents=True)
    (destination / "map.txt").write_text("map", encoding="utf-8")
    recoverable = settings.map_root / (
        f".delete-map-{map_id}-0123456789abcdef0123456789abcdef.tmp"
    )
    destination.replace(recoverable)
    committed = settings.map_root / (
        ".delete-map-999-fedcba9876543210fedcba9876543210.tmp"
    )
    committed.mkdir()

    Reconciler(settings, database, FakeRuntime()).run()

    assert (destination / "map.txt").read_text(encoding="utf-8") == "map"
    assert not recoverable.exists()
    assert not committed.exists()


def test_reconcile_recovers_game_and_backup_delete_trash(settings: Settings) -> None:
    database, _map_id, game_id = create_map_and_game(settings)
    game = settings.game_root / str(game_id)
    backups = settings.backup_root / str(game_id)
    game.mkdir(parents=True)
    backups.mkdir(parents=True)
    (game / "game.txt").write_text("game", encoding="utf-8")
    (backups / "backup.txt").write_text("backup", encoding="utf-8")
    game_trash = settings.game_root / (
        f".delete-game-{game_id}-0123456789abcdef0123456789abcdef.tmp"
    )
    backup_trash = settings.backup_root / (
        f".delete-game-{game_id}-fedcba9876543210fedcba9876543210.tmp"
    )
    game.replace(game_trash)
    backups.replace(backup_trash)
    committed = settings.game_root / (
        ".delete-game-999-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tmp"
    )
    committed.mkdir()

    Reconciler(settings, database, FakeRuntime()).run()

    assert (game / "game.txt").read_text(encoding="utf-8") == "game"
    assert (backups / "backup.txt").read_text(encoding="utf-8") == "backup"
    assert not game_trash.exists()
    assert not backup_trash.exists()
    assert not committed.exists()
