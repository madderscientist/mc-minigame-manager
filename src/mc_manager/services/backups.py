from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mc_manager.config import Settings
from mc_manager.enums import ResourceState
from mc_manager.errors import NotFoundError, ValidationError
from mc_manager.models import BackupRecord, GameRecord
from mc_manager.services.storage import Storage


class BackupService:
    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage

    @staticmethod
    def _base_backup_id(moment: datetime) -> str:
        return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")

    def _next_backup_id(self, session: Session, game_id: int) -> str:
        moment = datetime.now(UTC)
        for offset in range(1000):
            backup_id = self._base_backup_id(moment + timedelta(microseconds=offset))
            exists = session.scalar(
                select(BackupRecord.id).where(
                    BackupRecord.game_id == game_id,
                    BackupRecord.backup_id == backup_id,
                )
            )
            if exists is None:
                return backup_id
        raise RuntimeError("无法生成唯一备份时间标识")

    def create(
        self,
        session: Session,
        game: GameRecord,
        *,
        reason: str,
        clean_shutdown: bool,
        pinned_backup_id: str | None = None,
        rotate_after: bool = True,
    ) -> BackupRecord:
        if game.state != ResourceState.READY:
            raise ValidationError("game_not_ready", "只有可用游戏可以备份")
        source = self.storage.resolve(game.relative_path)
        if not source.exists():
            raise NotFoundError("game_files_missing", "游戏文件不存在")

        backup_id = self._next_backup_id(session, game.game_id)
        relative = f"backups/{game.game_id}/{backup_id}"
        destination = self.storage.resolve(relative)
        self.storage.copy_tree_atomic(source, destination, prefix=f"backup-{game.game_id}")
        sha256, size = self.storage.tree_digest(destination)
        record = BackupRecord(
            game_id=game.game_id,
            backup_id=backup_id,
            relative_path=relative,
            reason=reason,
            clean_shutdown=clean_shutdown,
            size_bytes=size,
            sha256=sha256,
        )
        session.add(record)
        session.flush()
        if rotate_after:
            self.rotate(session, game.game_id, pinned_backup_id=pinned_backup_id)
        return record

    def rotate(
        self,
        session: Session,
        game_id: int,
        *,
        pinned_backup_id: str | None = None,
    ) -> None:
        records = list(
            session.scalars(
                select(BackupRecord)
                .where(
                    BackupRecord.game_id == game_id,
                    BackupRecord.retained.is_(True),
                )
                .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
            ).all()
        )
        keep = self.settings.backup_limit
        removable = [record for record in reversed(records) if record.backup_id != pinned_backup_id]
        while len(records) > keep and removable:
            victim = removable.pop(0)
            victim.retained = False
            records.remove(victim)

    def collect_garbage(self, session: Session) -> int:
        records = list(
            session.scalars(
                select(BackupRecord).where(BackupRecord.retained.is_(False))
            ).all()
        )
        for record in records:
            path = self.storage.resolve(record.relative_path)
            if path.exists():
                import shutil

                shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                continue
            session.delete(record)
        session.flush()
        return len(records)

    def restore(self, session: Session, game: GameRecord, backup_id: str) -> BackupRecord:
        backup = session.scalar(
            select(BackupRecord).where(
                BackupRecord.game_id == game.game_id,
                BackupRecord.backup_id == backup_id,
                BackupRecord.retained.is_(True),
            )
        )
        if backup is None:
            raise NotFoundError("backup_not_found", "指定备份不存在或不属于该游戏")
        source = self.storage.resolve(backup.relative_path)
        destination = self.storage.resolve(game.relative_path)
        expected_sha = backup.sha256
        actual_sha, _ = self.storage.tree_digest(source)
        if actual_sha != expected_sha:
            legacy_sha, _ = self.storage.legacy_tree_digest(source)
            if legacy_sha != expected_sha:
                raise ValidationError("backup_corrupt", "备份校验失败, 拒绝恢复")
            backup.sha256 = actual_sha
        self.storage.replace_tree_atomic(
            source, destination, prefix=f"restore-game-{game.game_id}-{backup.backup_id}"
        )
        return backup
