import logging
import re
import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from mc_manager.config import Settings
from mc_manager.db import Database
from mc_manager.enums import ObservedState, TaskStatus
from mc_manager.models import BackupRecord, RunRecord, TaskRecord
from mc_manager.runtime.base import RuntimeBackend
from mc_manager.services.backups import BackupService
from mc_manager.services.ports import PortService
from mc_manager.services.storage import Storage

logger = logging.getLogger(__name__)
ROLLBACK_PATTERN = re.compile(r"^restore-game-(?P<game_id>\d+)-.+-old-[0-9a-f]+$")


class Reconciler:
    def __init__(self, settings: Settings, database: Database, runtime: RuntimeBackend) -> None:
        self.settings = settings
        self.database = database
        self.runtime = runtime

    def run(self) -> None:
        self._recover_restore_directories()
        self._clean_orphan_backup_directories()
        runtime_ids = self.runtime.managed_run_ids()
        with self.database.session_factory() as session:
            known_ids = set(session.scalars(select(RunRecord.run_id)).all())
            for orphan_id in runtime_ids - known_ids:
                logger.warning("Stopping orphan managed runtime %s", orphan_id)
                self.runtime.stop(orphan_id)

            runs = list(
                session.scalars(
                    select(RunRecord).where(
                        RunRecord.observed_state.in_(
                            {
                                ObservedState.STARTING,
                                ObservedState.READY,
                                ObservedState.STOPPING,
                                ObservedState.BACKING_UP,
                                ObservedState.UNKNOWN,
                            }
                        )
                    )
                ).all()
            )
            for run in runs:
                if not self.runtime.exists(run.run_id):
                    try:
                        self.runtime.stop(run.run_id, timeout_seconds=10)
                    except Exception:
                        logger.exception("Unable to remove inactive runtime %s", run.run_id)
                    run.observed_state = ObservedState.FAILED
                    run.last_error = "reconcile: runtime missing after manager restart"
                    run.stopped_at = datetime.now(UTC)
                    PortService.release(session, run.run_id, run.generation)
                    continue
                if run.observed_state == ObservedState.STARTING and self.runtime.wait_ready(
                    run.run_id, run.port, 2
                ):
                    run.observed_state = ObservedState.READY
                    run.ready_at = datetime.now(UTC)
                    try:
                        PortService.activate(session, run.run_id, run.generation)
                    except Exception:
                        logger.exception("Unable to activate reconciled port %s", run.port)
            session.commit()

    def _recover_restore_directories(self) -> None:
        now = datetime.now(UTC)
        self.settings.worker_staging_root.mkdir(parents=True, exist_ok=True)
        for path in self.settings.worker_staging_root.iterdir():
            if not path.is_dir():
                continue
            match = ROLLBACK_PATTERN.match(path.name)
            if match:
                game = self.settings.game_root / match.group("game_id")
                if not game.exists():
                    logger.warning(
                        "Recovering interrupted backup restore for game %s",
                        match.group("game_id"),
                    )
                    path.rename(game)
                else:
                    shutil.rmtree(path, ignore_errors=True)
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if now - modified > timedelta(hours=24):
                shutil.rmtree(path, ignore_errors=True)

        with self.database.session_factory.begin() as session:
            interrupted = list(
                session.scalars(
                    select(TaskRecord).where(TaskRecord.status == TaskStatus.RUNNING)
                ).all()
            )
            for task in interrupted:
                task.status = TaskStatus.PENDING
                task.claimed_by = None
                task.lease_expires_at = None
                task.step = f"retry:{task.step}"

        storage = Storage(self.settings.storage_root, self.settings.worker_staging_root)
        backups = BackupService(self.settings, storage)
        with self.database.session_factory.begin() as session:
            backups.collect_garbage(session)

    def _clean_orphan_backup_directories(self) -> None:
        if not self.settings.backup_root.exists():
            return
        with self.database.session_factory() as session:
            referenced = set(session.scalars(select(BackupRecord.relative_path)).all())
        now = datetime.now(UTC)
        for game_directory in self.settings.backup_root.iterdir():
            if not game_directory.is_dir():
                continue
            for backup_directory in game_directory.iterdir():
                if not backup_directory.is_dir():
                    continue
                relative = backup_directory.relative_to(self.settings.storage_root).as_posix()
                modified = datetime.fromtimestamp(backup_directory.stat().st_mtime, tz=UTC)
                if relative not in referenced and now - modified > timedelta(hours=24):
                    logger.warning("Removing orphan backup directory %s", backup_directory)
                    shutil.rmtree(backup_directory, ignore_errors=True)
