import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from mc_manager.config import Settings
from mc_manager.db import Database
from mc_manager.enums import ObservedState, TaskStatus
from mc_manager.models import BackupRecord, GameRecord, MapRecord, RunRecord, TaskRecord
from mc_manager.runtime.base import RuntimeBackend
from mc_manager.services.backups import BackupService
from mc_manager.services.ports import PortService
from mc_manager.services.storage import Storage

logger = logging.getLogger(__name__)
RESTORE_PATTERN = re.compile(
    r"^\.restore-game-(?P<game_id>\d+)-[^/]+-(?P<kind>new|old)-[0-9a-f]{32}\.tmp$"
)
DELETE_MAP_PATTERN = re.compile(r"^\.delete-map-(?P<map_id>\d+)-[0-9a-f]{32}\.tmp$")
DELETE_GAME_PATTERN = re.compile(
    r"^\.delete-game-(?P<game_id>\d+)-[0-9a-f]{32}\.tmp$"
)


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
        for root in (
            self.settings.map_root,
            self.settings.game_root,
            self.settings.backup_root,
            self.settings.worker_staging_root,
        ):
            root.mkdir(parents=True, exist_ok=True)
        storage = Storage(self.settings.storage_root, self.settings.worker_staging_root)
        with self.database.session_factory() as session:
            map_paths = {
                record.map_id: storage.resolve(record.relative_path)
                for record in session.scalars(select(MapRecord)).all()
            }
            game_paths = {
                record.game_id: storage.resolve(record.relative_path)
                for record in session.scalars(select(GameRecord)).all()
            }

        restore_directories: list[tuple[Path, re.Match[str]]] = []
        for path in self.settings.game_root.iterdir():
            if not path.is_dir():
                continue
            match = RESTORE_PATTERN.fullmatch(path.name)
            if match is not None:
                restore_directories.append((path, match))
        restore_directories.sort(key=lambda item: item[1].group("kind") != "old")
        for path_value, match_value in restore_directories:
            path = path_value
            match = match_value
            game_id = int(match.group("game_id"))
            destination = game_paths.get(game_id)
            if (
                match.group("kind") == "old"
                and destination is not None
                and not destination.exists()
            ):
                logger.warning(
                    "Recovering interrupted backup restore for game %s", game_id
                )
                path.replace(destination)
                continue
            shutil.rmtree(path, ignore_errors=True)

        self._recover_delete_directories(
            self.settings.map_root,
            DELETE_MAP_PATTERN,
            "map_id",
            map_paths,
        )
        for root in (self.settings.game_root, self.settings.backup_root):
            self._recover_delete_directories(
                root,
                DELETE_GAME_PATTERN,
                "game_id",
                {
                    game_id: (
                        path if root == self.settings.game_root else root / str(game_id)
                    )
                    for game_id, path in game_paths.items()
                },
            )

        self.settings.worker_staging_root.mkdir(parents=True, exist_ok=True)
        for path in self.settings.worker_staging_root.iterdir():
            if not path.is_dir():
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

        backups = BackupService(self.settings, storage)
        with self.database.session_factory.begin() as session:
            backups.collect_garbage(session)

    @staticmethod
    def _recover_delete_directories(
        root: Path,
        pattern: re.Pattern[str],
        identifier_group: str,
        destinations: dict[int, Path],
    ) -> None:
        for trash in root.iterdir():
            if not trash.is_dir():
                continue
            match = pattern.fullmatch(trash.name)
            if match is None:
                continue
            identifier = int(match.group(identifier_group))
            destination = destinations.get(identifier)
            if destination is not None and not destination.exists():
                logger.warning("Recovering interrupted deletion from %s", trash)
                trash.replace(destination)
            else:
                logger.warning("Removing committed deletion trash %s", trash)
                shutil.rmtree(trash, ignore_errors=True)

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
