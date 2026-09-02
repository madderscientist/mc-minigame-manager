import fcntl
import logging
import os
import shutil
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from mc_manager.config import Settings, get_settings
from mc_manager.db import Database
from mc_manager.enums import (
    DesiredState,
    ObservedState,
    ResourceState,
    TaskStatus,
    TaskType,
)
from mc_manager.errors import ManagerError
from mc_manager.models import GameRecord, RunRecord, TaskRecord
from mc_manager.reconcile import Reconciler
from mc_manager.runtime import RuntimeBackend, build_runtime
from mc_manager.runtime.base import RuntimeSpec
from mc_manager.services.artifacts import ArtifactManager
from mc_manager.services.backups import BackupService
from mc_manager.services.ports import PortService
from mc_manager.services.server_properties import (
    PAPER_PERMISSION_PROPERTIES,
    update_server_operators,
    update_server_properties,
)
from mc_manager.services.storage import Storage

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        runtime: RuntimeBackend,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.runtime = runtime
        self.worker_id = worker_id or f"{socket.gethostname()}-{id(self):x}"
        for path in (
            settings.game_root,
            settings.backup_root,
            settings.artifact_root,
            settings.worker_staging_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.storage = Storage(settings.storage_root, settings.worker_staging_root)
        self.backups = BackupService(settings, self.storage)
        self.ports = PortService(settings.port_min, settings.port_max)
        self.artifacts = ArtifactManager(
            settings.artifact_root,
            user_agent=settings.papermc_user_agent,
            allow_unstable=settings.allow_unstable_paper,
            allowed_hosts=settings.allowed_paper_hosts,
            max_artifact_bytes=settings.max_artifact_bytes,
        )

    def claim_one(self) -> str | None:
        now = datetime.now(UTC)
        with self.database.session_factory() as session:
            candidates = list(
                session.scalars(
                    select(TaskRecord.task_id)
                    .where(
                        TaskRecord.next_attempt_at <= now,
                        or_(
                            TaskRecord.status == TaskStatus.PENDING,
                            and_(
                                TaskRecord.status == TaskStatus.RUNNING,
                                TaskRecord.lease_expires_at < now,
                            ),
                        ),
                    )
                    .order_by(TaskRecord.created_at)
                    .limit(10)
                ).all()
            )
            for task_id in candidates:
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(TaskRecord)
                        .where(
                            TaskRecord.task_id == task_id,
                            or_(
                                TaskRecord.status == TaskStatus.PENDING,
                                and_(
                                    TaskRecord.status == TaskStatus.RUNNING,
                                    TaskRecord.lease_expires_at < now,
                                ),
                            ),
                        )
                        .values(
                            status=TaskStatus.RUNNING,
                            claimed_by=self.worker_id,
                            lease_expires_at=now
                            + timedelta(seconds=self.settings.task_lease_seconds),
                            attempt_count=TaskRecord.attempt_count + 1,
                        )
                    ),
                )
                if result.rowcount == 1:
                    session.commit()
                    return task_id
                session.rollback()
        return None

    def run_once(self) -> bool:
        task_id = self.claim_one()
        if task_id is None:
            return False
        try:
            with self.database.session_factory() as session:
                task_type = self._get_task(session, task_id).type
            if task_type == TaskType.CREATE_GAME:
                self._process_create_game(task_id)
            elif task_type == TaskType.DELETE_GAME:
                self._process_delete_game(task_id)
            elif task_type == TaskType.START:
                self._process_start(task_id)
            elif task_type == TaskType.STOP:
                self._process_stop(task_id)
            elif task_type == TaskType.LOAD_BACKUP:
                self._process_load(task_id)
            else:
                raise RuntimeError(f"Unsupported task type: {task_type}")
        except Exception as error:
            logger.exception("Task %s failed", task_id)
            self._fail(task_id, error)
        return True

    def _process_create_game(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            task = self._get_task(session, task_id)
            game = self._get_game(session, task)
            source = game.map
            task.step = "copying_map"
            task.progress = 0.2
            session.commit()
            source_path = self.storage.resolve(source.relative_path)
            game_path = self.storage.resolve(game.relative_path)
            if not game_path.exists():
                self.storage.copy_tree_atomic(
                    source_path,
                    game_path,
                    prefix=f"create-game-{game.game_id}",
                )
            game.content_sha256, _ = self.storage.tree_digest(game_path)
            game.state = ResourceState.READY
            self._unlock(game, task_id)
            self._succeed(task, {"game_id": game.game_id, "map_id": game.map_id})
            session.commit()

    def _process_delete_game(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            task = self._get_task(session, task_id)
            game = self._get_game(session, task)
            game_id = game.game_id
            task.step = "deleting_game"
            task.progress = 0.3
            session.commit()

            moved: list[tuple[Path, Path]] = []
            for source in (
                self.storage.resolve(game.relative_path),
                self.settings.backup_root / str(game_id),
            ):
                if source.exists():
                    trash = self.storage.temporary_sibling(
                        source, f"delete-game-{game_id}"
                    )
                    os.replace(source, trash)
                    moved.append((source, trash))
            try:
                self._succeed(task, {"game_id": game_id, "deleted": True})
                session.delete(game)
                session.commit()
            except Exception:
                session.rollback()
                for source, trash in reversed(moved):
                    if trash.exists() and not source.exists():
                        os.replace(trash, source)
                raise
            for _, trash in moved:
                shutil.rmtree(trash, ignore_errors=True)

    def _process_start(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            task = self._get_task(session, task_id)
            run = self._get_run(session, task)
            game = self._get_game(session, task)
            if run.desired_state == DesiredState.STOPPED:
                self.ports.release(session, run.run_id, run.generation)
                run.observed_state = ObservedState.STOPPED
                run.stopped_at = datetime.now(UTC)
                task.status = TaskStatus.CANCELED
                task.step = "canceled_before_runtime_start"
                task.finished_at = datetime.now(UTC)
                task.progress = 1.0
                self._unlock(game, task_id)
                session.commit()
                return

            task.step = "preparing_runtime"
            task.progress = 0.25
            run.observed_state = ObservedState.STARTING
            session.commit()
            game_path = self.storage.resolve(game.relative_path)
            self.artifacts.accept_eula(game_path)
            update_server_properties(
                game_path / "server.properties",
                PAPER_PERMISSION_PROPERTIES,
            )
            update_server_operators(
                game_path / "ops.json",
                self.settings.default_operators,
            )
            source = game.map
            if self.settings.runtime_backend in {"docker", "podman"}:
                if source.paper_url and source.paper_sha256:
                    paper_artifact = self.artifacts.ensure_paper(
                        source.paper_url, source.paper_sha256
                    )
                else:
                    resolved = self.artifacts.resolve_paper(
                        source.mc_version, source.paper_build
                    )
                    paper_artifact = self.artifacts.ensure_paper(
                        resolved.url, resolved.sha256
                    )
            else:
                paper_artifact = None
            image = self.settings.java_images.get(source.java_major)
            if image is None:
                raise RuntimeError(f"No Java image configured for Java {source.java_major}")
            runtime_spec = RuntimeSpec(
                run_id=run.run_id,
                container_name=run.container_name,
                game_path=game_path,
                port=run.port,
                image=image,
                java_memory=self.settings.container_memory,
                paper_jar=paper_artifact,
            )

        container_id = self.runtime.start(runtime_spec)
        with self.database.session_factory.begin() as session:
            task = self._get_task(session, task_id)
            run = self._get_run(session, task)
            task.step = "waiting_for_paper"
            task.progress = 0.65
            run.container_id = container_id

        if not self.runtime.wait_ready(
            runtime_spec.run_id,
            runtime_spec.port,
            self.settings.start_timeout_seconds,
        ):
            raise RuntimeError("Paper did not become ready before timeout")

        with self.database.session_factory.begin() as session:
            task = self._get_task(session, task_id)
            run = self._get_run(session, task)
            game = self._get_game(session, task)
            self.ports.activate(session, run.run_id, run.generation)
            run.observed_state = ObservedState.READY
            run.ready_at = datetime.now(UTC)
            game.last_played_at = datetime.now(UTC)
            self._unlock(game, task_id)
            self._succeed(
                task,
                {"game_id": game.game_id, "port": run.port},
            )

    def _process_stop(self, task_id: str) -> None:
        with self.database.session_factory.begin() as session:
            task = self._get_task(session, task_id)
            run = self._get_run(session, task)
            run.desired_state = DesiredState.STOPPED
            run.observed_state = ObservedState.STOPPING
            task.step = "stopping_paper"
            task.progress = 0.2

        clean = self.runtime.stop(run.run_id, timeout_seconds=120)
        with self.database.session_factory() as session:
            task = self._get_task(session, task_id)
            run = self._get_run(session, task)
            game = self._get_game(session, task)
            run.observed_state = ObservedState.BACKING_UP
            task.step = "backing_up"
            task.progress = 0.6
            session.commit()
            backup = self.backups.create(
                session,
                game,
                reason="normal_stop" if clean else "crash_snapshot",
                clean_shutdown=clean,
            )
            self.ports.release(session, run.run_id, run.generation)
            run.observed_state = ObservedState.STOPPED
            run.stopped_at = datetime.now(UTC)
            self._unlock(game, task_id)
            self._succeed(
                task,
                {
                    "game_id": game.game_id,
                    "backup_id": backup.backup_id,
                    "clean_shutdown": clean,
                },
            )
            session.commit()
        self._collect_backup_garbage()

    def _process_load(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            task = self._get_task(session, task_id)
            game = self._get_game(session, task)
            run = session.get(RunRecord, task.run_id) if task.run_id else None
            if run is not None and run.observed_state in {
                ObservedState.PREPARING,
                ObservedState.STARTING,
                ObservedState.READY,
                ObservedState.STOPPING,
                ObservedState.BACKING_UP,
                ObservedState.UNKNOWN,
            }:
                task.step = "stopping_for_restore"
                task.progress = 0.15
                run.observed_state = ObservedState.STOPPING
                session.commit()
                clean = self.runtime.stop(run.run_id, timeout_seconds=120)
                run.observed_state = ObservedState.STOPPED
                run.stopped_at = datetime.now(UTC)
                self.ports.release(session, run.run_id, run.generation)
                session.commit()
            else:
                clean = True

            task.step = "protecting_current_state"
            task.progress = 0.4
            session.commit()
            protection = self.backups.create(
                session,
                game,
                reason="before_restore",
                clean_shutdown=clean,
                pinned_backup_id=task.backup_id,
                rotate_after=False,
            )
            session.commit()
            task.step = "restoring_backup"
            task.progress = 0.7
            session.commit()
            if task.backup_id is None:
                raise RuntimeError("Load task has no backup ID")
            restored = self.backups.restore(session, game, task.backup_id)
            self.backups.rotate(session, game.game_id)
            self._unlock(game, task_id)
            self._succeed(
                task,
                {
                    "game_id": game.game_id,
                    "restored_backup_id": restored.backup_id,
                    "protection_backup_id": protection.backup_id,
                },
            )
            session.commit()
        self._collect_backup_garbage()

    def _fail(self, task_id: str, error: Exception) -> None:
        public_code, public_message = self._public_error(error)
        with self.database.session_factory() as session:
            task = session.get(TaskRecord, task_id)
            if task is None or task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELED}:
                return
            if task.run_id:
                run = session.get(RunRecord, task.run_id)
                if run is not None:
                    runtime_exists = True
                    if task.type == TaskType.START:
                        try:
                            self.runtime.stop(run.run_id, timeout_seconds=10)
                            runtime_exists = self.runtime.exists(run.run_id)
                        except Exception:
                            logger.exception("Failed to clean runtime after start failure")
                    else:
                        try:
                            runtime_exists = self.runtime.exists(run.run_id)
                        except Exception:
                            logger.exception("Failed to inspect runtime after task failure")
                    if runtime_exists:
                        run.observed_state = ObservedState.UNKNOWN
                    else:
                        self.ports.release(session, run.run_id, run.generation)
                        run.observed_state = (
                            ObservedState.FAILED
                            if task.type == TaskType.START
                            else ObservedState.STOPPED
                        )
                        run.stopped_at = datetime.now(UTC)
                    run.last_error = public_message
            if task.game_id:
                game = session.get(GameRecord, task.game_id)
                if game is not None:
                    if task.type == TaskType.CREATE_GAME:
                        game.state = ResourceState.FAILED
                    self._unlock(game, task_id)
            task.status = TaskStatus.FAILED
            task.error_code = public_code
            task.error_message = public_message
            task.step = "failed"
            task.progress = 1.0
            task.finished_at = datetime.now(UTC)
            session.commit()

    @staticmethod
    def _public_error(error: Exception) -> tuple[str, str]:
        if isinstance(error, ManagerError):
            return error.code, error.message
        if isinstance(error, OSError):
            return "storage_error", "存储操作失败, 请查看服务日志"
        return "task_failed", "任务执行失败, 请查看服务日志"

    def _collect_backup_garbage(self) -> None:
        with self.database.session_factory.begin() as session:
            self.backups.collect_garbage(session)

    @staticmethod
    def _get_task(session: Session, task_id: str) -> TaskRecord:
        task = session.get(TaskRecord, task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} does not exist")
        return task

    @staticmethod
    def _get_run(session: Session, task: TaskRecord) -> RunRecord:
        if task.run_id is None:
            raise RuntimeError("Task has no run")
        run = session.get(RunRecord, task.run_id)
        if run is None:
            raise RuntimeError("Run does not exist")
        return run

    @staticmethod
    def _get_game(session: Session, task: TaskRecord) -> GameRecord:
        if task.game_id is None:
            raise RuntimeError("Task has no game")
        game = session.get(GameRecord, task.game_id)
        if game is None:
            raise RuntimeError("Game does not exist")
        if game.task_lock_id != task.task_id:
            raise RuntimeError("Task no longer owns the game lock")
        return game

    @staticmethod
    def _unlock(game: GameRecord, task_id: str) -> None:
        if game.task_lock_id == task_id:
            game.task_lock_id = None

    @staticmethod
    def _succeed(task: TaskRecord, result: dict[str, object]) -> None:
        task.status = TaskStatus.SUCCEEDED
        task.step = "completed"
        task.progress = 1.0
        task.result = result
        task.finished_at = datetime.now(UTC)
        task.claimed_by = None
        task.lease_expires_at = None


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database = Database(settings)
    database.initialize()
    runtime_directory = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    runtime_directory.mkdir(parents=True, exist_ok=True)
    lock_file = (runtime_directory / "mc-manager-worker.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error("Another mc-manager Worker is already running")
        return
    runtime = build_runtime(settings)
    Reconciler(settings, database, runtime).run()
    worker = Worker(settings, database, runtime)
    logger.info("Worker %s started", worker.worker_id)
    last_gc = 0.0
    try:
        while True:
            if not worker.run_once():
                now = time.monotonic()
                if now - last_gc >= 60:
                    worker._collect_backup_garbage()
                    last_gc = now
                time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
