import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mc_manager.config import Settings
from mc_manager.enums import (
    DesiredState,
    ObservedState,
    ResourceState,
    TaskStatus,
    TaskType,
)
from mc_manager.errors import ConflictError, NotFoundError, ValidationError
from mc_manager.models import (
    BackupRecord,
    GameRecord,
    MapRecord,
    PortLease,
    RunRecord,
    TaskRecord,
)
from mc_manager.services.ports import PortService

LIVE_STATES = {
    ObservedState.PREPARING,
    ObservedState.STARTING,
    ObservedState.READY,
    ObservedState.STOPPING,
    ObservedState.BACKING_UP,
    ObservedState.UNKNOWN,
}


def request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class TaskService:
    def __init__(self, settings: Settings) -> None:
        self.ports = PortService(settings.port_min, settings.port_max)

    @staticmethod
    def _idempotent(
        session: Session, key: str | None, expected_hash: str
    ) -> TaskRecord | None:
        if not key:
            return None
        task = session.scalar(select(TaskRecord).where(TaskRecord.idempotency_key == key))
        if task is None:
            return None
        if task.request_hash != expected_hash:
            raise ConflictError(
                "idempotency_key_reused", "同一 Idempotency-Key 不能用于不同请求"
            )
        return task

    def create_game(
        self,
        session: Session,
        *,
        map_id: int,
        name: str | None,
        server_settings: Mapping[str, object] | None,
        idempotency_key: str | None,
    ) -> tuple[TaskRecord, GameRecord]:
        source = session.get(MapRecord, map_id)
        if source is None or source.state != ResourceState.READY:
            raise NotFoundError("map_not_found", "仓库地图不存在或尚未准备完成")
        final_settings = dict(
            source.server_settings if server_settings is None else server_settings
        )
        payload_hash = request_hash(
            {
                "type": "create_game",
                "map_id": map_id,
                "name": name,
                "server_settings": final_settings,
            }
        )
        existing = self._idempotent(session, idempotency_key, payload_hash)
        if existing is not None:
            game = session.get(GameRecord, existing.game_id)
            if game is None:
                raise ConflictError("task_incomplete", "幂等任务缺少游戏记录")
            return existing, game

        task_id = str(uuid.uuid4())
        game = GameRecord(
            map_id=source.map_id,
            state=ResourceState.PREPARING,
            name=(name or source.name).strip(),
            relative_path=f"pending/{task_id}",
            task_lock_id=task_id,
            server_settings=final_settings,
        )
        session.add(game)
        session.flush()
        game.relative_path = f"games/{game.game_id}"
        task = TaskRecord(
            task_id=task_id,
            type=TaskType.CREATE_GAME,
            status=TaskStatus.PENDING,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            map_id=source.map_id,
            game_id=game.game_id,
        )
        session.add(task)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            existing = self._idempotent(session, idempotency_key, payload_hash)
            if existing is not None:
                existing_game = session.get(GameRecord, existing.game_id)
                if existing_game is not None:
                    return existing, existing_game
            raise ConflictError("concurrent_task", "创建游戏时发生并发冲突") from error
        return task, game

    def create_start(
        self,
        session: Session,
        *,
        game_id: int,
        port: int | None,
        idempotency_key: str | None,
    ) -> tuple[TaskRecord, RunRecord]:
        payload_hash = request_hash({"type": "start", "game_id": game_id, "port": port})
        existing = self._idempotent(session, idempotency_key, payload_hash)
        if existing is not None:
            run = session.get(RunRecord, existing.run_id)
            if run is None:
                raise ConflictError("task_incomplete", "幂等任务缺少运行记录")
            return existing, run

        game = session.get(GameRecord, game_id)
        if game is None or game.state != ResourceState.READY:
            raise NotFoundError("game_not_found", "游戏不存在或尚未准备完成")
        if game.task_lock_id is not None:
            raise ConflictError("game_busy", "游戏正在执行其他任务")
        live = session.scalar(
            select(RunRecord.run_id).where(
                RunRecord.game_id == game_id,
                RunRecord.observed_state.in_(LIVE_STATES),
            )
        )
        if live is not None:
            raise ConflictError("game_already_running", "游戏已经启动或正在操作")

        task_id = str(uuid.uuid4())
        game.task_lock_id = task_id
        run_id = str(uuid.uuid4())
        run = RunRecord(
            run_id=run_id,
            game_id=game.game_id,
            port=0,
            desired_state=DesiredState.RUNNING,
            observed_state=ObservedState.PREPARING,
            container_name=f"mc-{run_id}",
            generation=1,
        )
        session.add(run)
        session.flush()
        allocated_port = self.ports.reserve(session, run_id, port)
        run.port = allocated_port
        lease = session.get(PortLease, allocated_port)
        if lease is None:
            raise RuntimeError("端口租约未创建")
        run.generation = lease.generation
        task = TaskRecord(
            task_id=task_id,
            type=TaskType.START,
            status=TaskStatus.PENDING,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            map_id=game.map_id,
            game_id=game.game_id,
            requested_port=port,
            run_id=run_id,
        )
        session.add(task)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            existing = self._idempotent(session, idempotency_key, payload_hash)
            if existing is not None and existing.run_id is not None:
                existing_run = session.get(RunRecord, existing.run_id)
                if existing_run is not None:
                    return existing, existing_run
            raise ConflictError("concurrent_task", "游戏启动发生并发冲突") from error
        return task, run

    def create_delete_game(
        self,
        session: Session,
        *,
        game_id: int,
        idempotency_key: str | None,
    ) -> TaskRecord:
        payload_hash = request_hash({"type": "delete_game", "game_id": game_id})
        existing = self._idempotent(session, idempotency_key, payload_hash)
        if existing is not None:
            return existing
        game = session.get(GameRecord, game_id)
        if game is None:
            raise NotFoundError("game_not_found", "游戏不存在")
        if game.task_lock_id is not None:
            raise ConflictError("game_busy", "游戏正在执行其他任务")
        live = session.scalar(
            select(RunRecord.run_id).where(
                RunRecord.game_id == game_id,
                RunRecord.observed_state.in_(LIVE_STATES),
            )
        )
        if live is not None:
            raise ConflictError("game_running", "运行中的游戏不能删除")

        task_id = str(uuid.uuid4())
        game.task_lock_id = task_id
        task = TaskRecord(
            task_id=task_id,
            type=TaskType.DELETE_GAME,
            status=TaskStatus.PENDING,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            map_id=game.map_id,
            game_id=game.game_id,
        )
        session.add(task)
        session.commit()
        return task

    def create_stop(
        self,
        session: Session,
        *,
        game_id: int,
        backup: bool,
        idempotency_key: str | None,
    ) -> tuple[TaskRecord, RunRecord]:
        payload_hash = request_hash({"type": "stop", "game_id": game_id, "backup": backup})
        existing = self._idempotent(session, idempotency_key, payload_hash)
        if existing is not None:
            run = session.get(RunRecord, existing.run_id)
            if run is None:
                raise NotFoundError("run_not_found", "任务对应的运行记录不存在")
            return existing, run

        game = session.get(GameRecord, game_id)
        if game is None:
            raise NotFoundError("game_not_found", "游戏不存在")
        run = session.scalar(
            select(RunRecord)
            .where(
                RunRecord.game_id == game_id,
                RunRecord.observed_state.in_(LIVE_STATES),
            )
            .order_by(RunRecord.created_at.desc())
        )
        if run is None:
            raise NotFoundError("game_not_running", "游戏当前没有运行")
        pending = session.scalar(
            select(TaskRecord).where(
                TaskRecord.run_id == run.run_id,
                TaskRecord.type == TaskType.STOP,
                TaskRecord.status.in_({TaskStatus.PENDING, TaskStatus.RUNNING}),
            )
        )
        if pending is not None:
            return pending, run
        if game.task_lock_id is not None:
            raise ConflictError("game_busy", "游戏正在执行其他任务")

        task_id = str(uuid.uuid4())
        game.task_lock_id = task_id
        run.desired_state = DesiredState.STOPPED
        task = TaskRecord(
            task_id=task_id,
            type=TaskType.STOP,
            status=TaskStatus.PENDING,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            map_id=game.map_id,
            game_id=game.game_id,
            requested_port=run.port,
            backup_requested=backup,
            run_id=run.run_id,
        )
        session.add(task)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            pending = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.run_id == run.run_id,
                    TaskRecord.type == TaskType.STOP,
                    TaskRecord.status.in_({TaskStatus.PENDING, TaskStatus.RUNNING}),
                )
            )
            if pending is not None:
                current_run = session.get(RunRecord, run.run_id)
                if current_run is not None:
                    return pending, current_run
            raise ConflictError("concurrent_stop", "游戏正在执行并发停止任务") from error
        return task, run

    def create_load(
        self,
        session: Session,
        *,
        game_id: int,
        backup_id: str,
        idempotency_key: str | None,
    ) -> TaskRecord:
        payload_hash = request_hash(
            {"type": "load_backup", "game_id": game_id, "backup_id": backup_id}
        )
        existing = self._idempotent(session, idempotency_key, payload_hash)
        if existing is not None:
            return existing
        game = session.get(GameRecord, game_id)
        if game is None or game.state != ResourceState.READY:
            raise ValidationError("game_required", "只能向可用游戏加载备份")
        if game.task_lock_id is not None:
            raise ConflictError("game_busy", "游戏正在执行其他任务")
        backup = session.scalar(
            select(BackupRecord.id).where(
                BackupRecord.game_id == game_id,
                BackupRecord.backup_id == backup_id,
                BackupRecord.retained.is_(True),
            )
        )
        if backup is None:
            raise NotFoundError("backup_not_found", "备份不存在或不属于该游戏")

        task_id = str(uuid.uuid4())
        game.task_lock_id = task_id
        live_run = session.scalar(
            select(RunRecord)
            .where(
                RunRecord.game_id == game_id,
                RunRecord.observed_state.in_(LIVE_STATES),
            )
            .order_by(RunRecord.created_at.desc())
        )
        if live_run is not None:
            live_run.desired_state = DesiredState.STOPPED
        task = TaskRecord(
            task_id=task_id,
            type=TaskType.LOAD_BACKUP,
            status=TaskStatus.PENDING,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            map_id=game.map_id,
            game_id=game.game_id,
            backup_id=backup_id,
            run_id=live_run.run_id if live_run else None,
        )
        session.add(task)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            existing = self._idempotent(session, idempotency_key, payload_hash)
            if existing is not None:
                return existing
            raise ConflictError("concurrent_task", "游戏正在执行并发任务") from error
        return task
