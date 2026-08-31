from sqlalchemy import select

from mc_manager.config import Settings
from mc_manager.db import Database
from mc_manager.enums import ResourceState
from mc_manager.models import BackupRecord, GameRecord, MapRecord
from mc_manager.services.backups import BackupService
from mc_manager.services.storage import Storage


def test_backup_window_removes_oldest_only_after_new_backup(settings: Settings) -> None:
    database = Database(settings)
    database.initialize()
    storage = Storage(settings.storage_root, settings.worker_staging_root)
    service = BackupService(settings, storage)

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
            relative_path="pending",
        )
        session.add(game)
        session.flush()
        game.relative_path = f"games/{game.game_id}"
        session.commit()
        game_path = settings.game_root / str(game.game_id)
        game_path.mkdir(parents=True)
        progress = game_path / "level.dat"
        progress.write_bytes(b"0")
        for number in range(5):
            progress.write_bytes(str(number).encode())
            service.create(session, game, reason="test", clean_shutdown=True)
            session.commit()
            service.collect_garbage(session)
            session.commit()

        records = list(
            session.scalars(
                select(BackupRecord)
                .where(BackupRecord.game_id == game.game_id)
                .order_by(BackupRecord.created_at)
            ).all()
        )
        assert len(records) == settings.backup_limit
        assert all(storage.resolve(record.relative_path).is_dir() for record in records)
