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
