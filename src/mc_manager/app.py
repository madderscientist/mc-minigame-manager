import logging
import os
import secrets
import shutil
import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Body, Depends, FastAPI, Header, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile

from mc_manager.config import Settings, get_settings
from mc_manager.db import Database
from mc_manager.enums import ResourceState
from mc_manager.errors import ConflictError, ManagerError, NotFoundError, ValidationError
from mc_manager.models import (
    BackupRecord,
    GameRecord,
    MapRecord,
    PortLease,
    RunRecord,
    TaskRecord,
)
from mc_manager.schemas import (
    BackupView,
    CreateGameAccepted,
    CreateGameRequest,
    DeleteGameAccepted,
    GameView,
    LoadAccepted,
    LoadRequest,
    MapView,
    PortView,
    RunningGameView,
    StartAccepted,
    StartRequest,
    StatusView,
    StopAccepted,
    StopRequest,
    TaskView,
    UploadResult,
)
from mc_manager.services.archive import SafeZipExtractor
from mc_manager.services.maps import MapService
from mc_manager.services.storage import Storage
from mc_manager.services.tasks import LIVE_STATES, TaskService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    database = Database(resolved_settings)
    storage = Storage(resolved_settings.storage_root, resolved_settings.api_staging_root)
    tasks = TaskService(resolved_settings)
    maps = MapService(storage, SafeZipExtractor(resolved_settings))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        for path in (
            resolved_settings.map_root,
            resolved_settings.upload_root,
            resolved_settings.api_staging_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        database.initialize()
        yield

    app = FastAPI(
        title="Minecraft Minigame Manager",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.database = database

    @app.middleware("http")
    async def authenticate_api(request: Request, call_next: Any) -> Any:
        configured = resolved_settings.api_token
        if request.url.path.startswith("/api/") and configured is not None:
            supplied = request.headers.get("Authorization", "")
            expected = f"Bearer {configured.get_secret_value()}"
            if not secrets.compare_digest(supplied, expected):
                return _error_response(401, "unauthorized", "缺少或无效的 Bearer token")
        return await call_next(request)

    def get_session() -> Generator[Session, None, None]:
        yield from database.session_dependency()

    SessionDependency = Annotated[Session, Depends(get_session)]
    IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]

    @app.exception_handler(ManagerError)
    async def manager_error_handler(_request: Request, error: ManagerError) -> JSONResponse:
        return _error_response(
            error.status_code,
            error.code,
            error.message,
            details=error.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            422,
            "request_validation_failed",
            "请求参数校验失败",
            details={"errors": error.errors()},
        )

    @app.exception_handler(OperationalError)
    async def database_busy_handler(
        _request: Request, error: OperationalError
    ) -> JSONResponse:
        logger.warning("Database operation failed: %s", error)
        response = _error_response(503, "database_busy", "数据库暂时繁忙, 请稍后重试")
        response.headers["Retry-After"] = "1"
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/maps", response_model=list[MapView])
    def list_maps(session: SessionDependency) -> list[MapRecord]:
        return list(
            session.scalars(
                select(MapRecord)
                .where(MapRecord.state == ResourceState.READY)
                .order_by(MapRecord.map_id)
            ).all()
        )

    @app.get("/api/maps/{map_id}", response_model=MapView)
    def get_map(map_id: int, session: SessionDependency) -> MapRecord:
        record = session.get(MapRecord, map_id)
        if record is None:
            raise NotFoundError("map_not_found", "仓库地图不存在")
        return record

    @app.post("/api/maps", response_model=UploadResult, status_code=201)
    async def upload_map(request: Request, session: SessionDependency) -> UploadResult:
        form = await request.form()
        map_upload: UploadFile | None = None
        resources: list[UploadFile] = []
        fields: dict[str, str] = {}
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                if key in {"map", "map.zip"} or value.filename == "map.zip":
                    if map_upload is not None:
                        raise ValidationError("duplicate_map", "只能上传一个 map.zip")
                    map_upload = value
                else:
                    resources.append(value)
            else:
                fields[key] = str(value)
        if map_upload is None:
            raise ValidationError("map_required", "multipart 请求必须包含 map.zip")

        upload_dir = resolved_settings.upload_root / uuid.uuid4().hex
        upload_dir.mkdir(parents=True, mode=0o700)
        try:
            map_path = upload_dir / "map.zip"
            await _save_upload(map_upload, map_path, resolved_settings.max_upload_bytes)
            resource_paths: list[Path] = []
            total = map_path.stat().st_size
            for index, resource in enumerate(resources, start=1):
                filename = Path(resource.filename or f"res{index}.zip").name
                if filename != (resource.filename or filename):
                    raise ValidationError("resource_name_invalid", "资源文件名包含路径")
                resource_path = upload_dir / filename
                remaining = resolved_settings.max_upload_bytes - total
                if remaining <= 0:
                    raise ValidationError("upload_too_large", "上传总大小超过限制")
                await _save_upload(resource, resource_path, remaining)
                total += resource_path.stat().st_size
                resource_paths.append(resource_path)

            try:
                java_major = int(fields.get("java_major", "17"))
            except ValueError as error:
                raise ValidationError("java_invalid", "java_major 必须是整数") from error
            record = maps.import_repository(
                session,
                archive_path=map_path,
                resource_paths=resource_paths,
                name=fields.get("name", ""),
                mc_version=fields.get("mc_version", ""),
                paper_build=fields.get("paper_build", ""),
                java_major=java_major,
                paper_url=fields.get("paper_url") or None,
                paper_sha256=fields.get("paper_sha256") or None,
            )
            session.commit()
            return UploadResult(
                map_id=record.map_id,
                name=record.name,
                mc_version=record.mc_version,
                resources=list(record.extra_metadata.get("resources", [])),
            )
        except Exception:
            session.rollback()
            raise
        finally:
            shutil.rmtree(upload_dir, ignore_errors=True)

    @app.delete("/api/maps/{map_id}", status_code=204)
    def delete_map(map_id: int, session: SessionDependency) -> Response:
        record = session.get(MapRecord, map_id)
        if record is None:
            raise NotFoundError("map_not_found", "仓库地图不存在")
        game_exists = session.scalar(select(GameRecord.game_id).where(GameRecord.map_id == map_id))
        if game_exists is not None:
            raise ConflictError("map_in_use", "存在由该地图创建的游戏, 不能删除")
        source = storage.resolve(record.relative_path)
        trash = storage.staging_path(f"delete-map-{map_id}")
        moved = False
        if source.exists():
            os.replace(source, trash)
            moved = True
        try:
            session.delete(record)
            session.commit()
        except Exception:
            session.rollback()
            if moved and trash.exists() and not source.exists():
                os.replace(trash, source)
            raise
        if moved:
            shutil.rmtree(trash, ignore_errors=True)
        return Response(status_code=204)

    @app.get("/api/games", response_model=list[GameView])
    def list_games(session: SessionDependency) -> list[GameView]:
        games = list(
            session.scalars(
                select(GameRecord)
                .options(selectinload(GameRecord.backups))
                .order_by(GameRecord.game_id)
            )
            .unique()
            .all()
        )
        return [_game_view(session, game) for game in games]

    @app.post(
        "/api/games",
        response_model=CreateGameAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_game(
        request: Annotated[CreateGameRequest, Body()],
        session: SessionDependency,
        idempotency_key: IdempotencyKey = None,
    ) -> CreateGameAccepted:
        task, game = tasks.create_game(
            session,
            map_id=request.map_id,
            name=request.name,
            idempotency_key=idempotency_key,
        )
        return CreateGameAccepted(
            task_id=task.task_id,
            game_id=game.game_id,
            map_id=game.map_id,
            status=task.status,
        )

    @app.get("/api/games/{game_id}", response_model=GameView)
    def get_game(game_id: int, session: SessionDependency) -> GameView:
        game = session.scalar(
            select(GameRecord)
            .options(selectinload(GameRecord.backups))
            .where(GameRecord.game_id == game_id)
        )
        if game is None:
            raise NotFoundError("game_not_found", "游戏不存在")
        return _game_view(session, game)

    @app.delete(
        "/api/games/{game_id}",
        response_model=DeleteGameAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def delete_game(
        game_id: int,
        session: SessionDependency,
        idempotency_key: IdempotencyKey = None,
    ) -> DeleteGameAccepted:
        task = tasks.create_delete_game(
            session,
            game_id=game_id,
            idempotency_key=idempotency_key,
        )
        return DeleteGameAccepted(task_id=task.task_id, game_id=game_id, status=task.status)

    @app.post(
        "/api/start",
        response_model=StartAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_game(
        request: Annotated[StartRequest, Body()],
        session: SessionDependency,
        idempotency_key: IdempotencyKey = None,
    ) -> StartAccepted:
        task, run = tasks.create_start(
            session,
            game_id=request.game_id,
            port=request.port,
            idempotency_key=idempotency_key,
        )
        return StartAccepted(
            task_id=task.task_id,
            game_id=run.game_id,
            port=run.port,
            status=task.status,
        )

    @app.post(
        "/api/stop",
        response_model=StopAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def stop_game(
        request: Annotated[StopRequest, Body()],
        session: SessionDependency,
        idempotency_key: IdempotencyKey = None,
    ) -> StopAccepted:
        task, run = tasks.create_stop(
            session,
            game_id=request.game_id,
            idempotency_key=idempotency_key,
        )
        return StopAccepted(task_id=task.task_id, game_id=run.game_id, status=task.status)

    @app.get("/api/games/{game_id}/backups", response_model=list[BackupView])
    def list_backups(game_id: int, session: SessionDependency) -> list[BackupRecord]:
        if session.get(GameRecord, game_id) is None:
            raise NotFoundError("game_not_found", "游戏不存在")
        return list(
            session.scalars(
                select(BackupRecord)
                .where(BackupRecord.game_id == game_id, BackupRecord.retained.is_(True))
                .order_by(BackupRecord.created_at.desc())
            ).all()
        )

    @app.post(
        "/api/load",
        response_model=LoadAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def load_backup(
        request: Annotated[LoadRequest, Body()],
        session: SessionDependency,
        idempotency_key: IdempotencyKey = None,
    ) -> LoadAccepted:
        task = tasks.create_load(
            session,
            game_id=request.game_id,
            backup_id=request.backup_id,
            idempotency_key=idempotency_key,
        )
        return LoadAccepted(
            task_id=task.task_id,
            game_id=request.game_id,
            backup_id=request.backup_id,
            status=task.status,
        )

    @app.delete("/api/games/{game_id}/backups/{backup_id}", status_code=204)
    def delete_backup(game_id: int, backup_id: str, session: SessionDependency) -> Response:
        game = session.get(GameRecord, game_id)
        if game is None:
            raise NotFoundError("game_not_found", "游戏不存在")
        if game.task_lock_id is not None:
            raise ConflictError("game_busy", "游戏正在执行其他任务")
        backup = session.scalar(
            select(BackupRecord).where(
                BackupRecord.game_id == game_id,
                BackupRecord.backup_id == backup_id,
                BackupRecord.retained.is_(True),
            )
        )
        if backup is None:
            raise NotFoundError("backup_not_found", "备份不存在")
        backup.retained = False
        session.commit()
        return Response(status_code=204)

    @app.get("/api/tasks/{task_id}", response_model=TaskView)
    def get_task(task_id: str, session: SessionDependency) -> TaskRecord:
        task = session.get(TaskRecord, task_id)
        if task is None:
            raise NotFoundError("task_not_found", "任务不存在")
        return task

    @app.get("/api/status", response_model=StatusView)
    def get_status(session: SessionDependency) -> StatusView:
        runs = list(
            session.scalars(
                select(RunRecord)
                .where(RunRecord.observed_state.in_(LIVE_STATES))
                .order_by(RunRecord.created_at.desc())
            ).all()
        )
        recent_tasks = list(
            session.scalars(
                select(TaskRecord).order_by(TaskRecord.created_at.desc()).limit(100)
            ).all()
        )
        leases = list(session.scalars(select(PortLease).order_by(PortLease.port)).all())
        all_run_games: dict[str, int] = {
            run_id: game_id
            for run_id, game_id in session.execute(
                select(RunRecord.run_id, RunRecord.game_id)
            ).tuples()
        }
        return StatusView(
            running_games=[
                RunningGameView(
                    game_id=run.game_id,
                    observed_state=run.observed_state,
                    port=run.port,
                    last_error=run.last_error,
                )
                for run in runs
            ],
            tasks=[TaskView.model_validate(task) for task in recent_tasks],
            ports=[
                PortView(
                    port=lease.port,
                    state=lease.state,
                    game_id=(
                        all_run_games.get(lease.run_id)
                        if lease.run_id is not None
                        else None
                    ),
                )
                for lease in leases
            ],
        )

    return app


def _game_view(session: Session, game: GameRecord) -> GameView:
    run = session.scalar(
        select(RunRecord)
        .where(RunRecord.game_id == game.game_id)
        .order_by(RunRecord.created_at.desc())
    )
    view = GameView.model_validate(game)
    if run is None:
        return view
    return view.model_copy(update={"runtime_state": run.observed_state, "port": run.port})


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        },
    )


async def _save_upload(upload: UploadFile, destination: Path, limit: int) -> None:
    written = 0
    with destination.open("xb") as output:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                raise ValidationError("upload_too_large", "上传内容超过大小限制")
            output.write(chunk)
    await upload.close()


app = create_app()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(
        "mc_manager.app:app",
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
