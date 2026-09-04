import asyncio
import fcntl
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from typing import Annotated, Any, BinaryIO, TypeAlias, cast

import uvicorn
from anyio import to_thread
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile

from mc_manager.config import Settings, get_settings
from mc_manager.db import Database
from mc_manager.enums import MapSourceType, ResourceState
from mc_manager.errors import ConflictError, ManagerError, NotFoundError, ValidationError
from mc_manager.middleware import RequestSizeLimitMiddleware
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
    GenerateMapRequest,
    LoadAccepted,
    LoadRequest,
    MapView,
    PaperVersionView,
    PortView,
    RunningGameView,
    ServerSettings,
    StartAccepted,
    StartRequest,
    StatusView,
    StopAccepted,
    StopRequest,
    TaskView,
    UploadResult,
)
from mc_manager.services.archive import SafeZipExtractor
from mc_manager.services.artifacts import latest_stable_paper_build, supported_paper_versions
from mc_manager.services.maps import (
    RESOURCE_PACK_STORAGE_NAME,
    SAFE_RESOURCE_NAME,
    MapService,
    ResourcePackImport,
)
from mc_manager.services.storage import Storage
from mc_manager.services.tasks import LIVE_STATES, TaskService
from mc_manager.services.uploads import ChunkedUploadStore
from mc_manager.services.versions import (
    read_data_version,
    read_minecraft_version_from_zip,
    required_java_major,
)

logger = logging.getLogger(__name__)
STATIC_ROOT = Path(__file__).resolve().parent / "static"


def get_session(request: Request) -> Generator[Session, None, None]:
    database = cast(Database, request.app.state.database)
    yield from database.session_dependency()


SessionDependency: TypeAlias = Annotated[Session, Depends(get_session)]
IdempotencyKey: TypeAlias = Annotated[str | None, Header(alias="Idempotency-Key")]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    database = Database(resolved_settings)
    storage = Storage(resolved_settings.storage_root, resolved_settings.api_staging_root)
    tasks = TaskService(resolved_settings)
    version_cache: tuple[float, list[tuple[str, int]]] | None = None
    chunked_uploads = ChunkedUploadStore(
        resolved_settings.upload_root,
        max_bytes=resolved_settings.max_upload_bytes,
        max_resource_pack_bytes=resolved_settings.max_resource_pack_bytes,
        max_sessions=resolved_settings.max_upload_sessions,
        max_reserved_bytes=resolved_settings.max_upload_reserved_bytes,
    )
    maps = MapService(
        storage,
        SafeZipExtractor(resolved_settings),
        resolved_settings.resource_pack_base_url,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        for path in (
            resolved_settings.map_root,
            resolved_settings.upload_root,
            resolved_settings.api_staging_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        database.initialize()
        await to_thread.run_sync(chunked_uploads.cleanup_expired)

        async def upload_gc_loop() -> None:
            while True:
                await asyncio.sleep(3600)
                try:
                    await to_thread.run_sync(chunked_uploads.cleanup_expired)
                except Exception:
                    logger.exception("Unable to clean expired upload sessions")

        upload_gc_task = asyncio.create_task(upload_gc_loop())
        try:
            yield
        finally:
            upload_gc_task.cancel()
            with suppress(asyncio.CancelledError):
                await upload_gc_task

    app = FastAPI(
        title="Minecraft Minigame Manager",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=resolved_settings.max_upload_bytes
        + resolved_settings.max_upload_overhead_bytes,
    )
    app.state.settings = resolved_settings
    app.state.database = database
    app.state.chunked_uploads = chunked_uploads

    @app.middleware("http")
    async def authenticate_api(request: Request, call_next: Any) -> Any:
        configured = resolved_settings.api_token
        if request.url.path.startswith("/api/") and configured is not None:
            supplied = request.headers.get("Authorization", "")
            expected = f"Bearer {configured.get_secret_value()}"
            if not secrets.compare_digest(supplied, expected):
                return _error_response(401, "unauthorized", "缺少或无效的 Bearer token")
        return await call_next(request)

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

    @app.get("/api/paper/versions", response_model=list[PaperVersionView])
    async def list_paper_versions() -> list[PaperVersionView]:
        nonlocal version_cache
        now = time.monotonic()
        if version_cache is None or now - version_cache[0] >= 300:
            versions = await to_thread.run_sync(
                supported_paper_versions,
                resolved_settings.papermc_user_agent,
            )
            version_cache = (now, versions)
        return [
            PaperVersionView(version=version, java_major=java_major)
            for version, java_major in version_cache[1]
        ]

    @app.get("/api/maps/{map_id}", response_model=MapView)
    def get_map(map_id: int, session: SessionDependency) -> MapRecord:
        record = session.get(MapRecord, map_id)
        if record is None:
            raise NotFoundError("map_not_found", "仓库地图不存在")
        return record

    @app.get(
        "/resource-packs/maps/{map_id}/{sha1}/{filename}",
        include_in_schema=False,
    )
    def download_resource_pack(
        map_id: int,
        sha1: str,
        filename: str,
        session: SessionDependency,
    ) -> FileResponse:
        record = session.get(MapRecord, map_id)
        metadata = record.resource_pack if record is not None else None
        if (
            record is None
            or record.state != ResourceState.READY
            or metadata is None
            or not secrets.compare_digest(str(metadata.get("sha1", "")), sha1)
            or str(metadata.get("filename", "")) != filename
        ):
            raise HTTPException(status_code=404, detail="Resource pack not found")
        resource_pack = (
            storage.resolve(record.relative_path)
            / ".mc-manager-resources"
            / RESOURCE_PACK_STORAGE_NAME
        )
        if not resource_pack.is_file():
            raise HTTPException(status_code=404, detail="Resource pack not found")
        return FileResponse(
            resource_pack,
            media_type="application/zip",
            filename=filename,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": f'"sha1-{sha1}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def resolve_paper_build(
        mc_version: str,
        requested_build: str | None,
        paper_url: str | None,
    ) -> str:
        if requested_build:
            return requested_build
        if paper_url:
            return "custom"
        with database.session_factory() as session:
            reusable = maps.latest_reusable_paper_build(session, mc_version)
        if reusable is not None:
            return reusable
        return await to_thread.run_sync(
            partial(
                latest_stable_paper_build,
                mc_version,
                user_agent=resolved_settings.papermc_user_agent,
            )
        )

    async def import_saved_map(
        map_path: Path,
        resource_pack_path: Path | None,
        fields: dict[str, str],
        idempotency_key: str | None,
    ) -> MapRecord:
        server_settings = _parse_server_settings(fields.get("server_settings"))
        if any(
            getattr(server_settings, field_name) is not None
            for field_name in ("level_seed", "level_type", "generate_structures")
        ):
            raise ValidationError(
                "world_generation_settings_not_allowed",
                "上传已有世界时不能设置种子、世界类型或生成结构",
            )
        map_digest, _ = await to_thread.run_sync(_file_digests, map_path)
        detected_version = await to_thread.run_sync(
            read_minecraft_version_from_zip, map_path
        )
        submitted_version = fields.get("mc_version", "").strip()
        if detected_version and submitted_version and detected_version != submitted_version:
            raise ValidationError(
                "mc_version_mismatch",
                f"level.dat 显示版本为 {detected_version}, 与填写的 {submitted_version} 不一致",
            )
        fields["mc_version"] = detected_version or submitted_version
        if not fields["mc_version"]:
            raise ValidationError(
                "mc_version_required",
                "无法从 level.dat 读取 Minecraft 版本",
            )

        resource_pack_import: ResourcePackImport | None = None
        resource_pack_digest: tuple[str, str] | None = None
        if resource_pack_path is not None:
            if resolved_settings.resource_pack_base_url is None:
                raise ValidationError(
                    "resource_pack_base_url_missing",
                    "后端未配置资源包公网地址, 暂时不能上传客户端资源包",
                )
            if resource_pack_path.stat().st_size > resolved_settings.max_resource_pack_bytes:
                raise ValidationError("resource_pack_too_large", "客户端资源包不能超过 250 MiB")
            submitted_filename = fields.get("resource_pack_filename", "")
            filename = Path(submitted_filename.replace("\\", "/")).name
            if not SAFE_RESOURCE_NAME.fullmatch(filename):
                filename = "resources.zip"
            sha256, sha1 = await to_thread.run_sync(_file_digests, resource_pack_path)
            resource_pack_digest = (filename, sha256)
            prompt = fields.get("resource_pack_prompt", "").strip() or None
            if prompt is not None and len(prompt) > 256:
                raise ValidationError(
                    "resource_pack_prompt_too_long", "资源包提示不能超过 256 个字符"
                )
            resource_pack_import = ResourcePackImport(
                path=resource_pack_path,
                filename=filename,
                sha1=sha1,
                sha256=sha256,
                size_bytes=resource_pack_path.stat().st_size,
                required=_parse_form_bool(fields.get("resource_pack_required"), False),
                prompt=prompt,
            )

        try:
            java_major = required_java_major(fields["mc_version"])
        except ValueError as error:
            raise ValidationError("mc_version_unsupported", str(error)) from error
        requested_paper_build = fields.get("paper_build", "").strip()
        hash_fields = dict(fields)
        paper_url = fields.get("paper_url", "").strip()
        fields["paper_build"] = await resolve_paper_build(
            fields["mc_version"], requested_paper_build or None, paper_url or None
        )
        if not requested_paper_build:
            hash_fields["paper_build"] = "automatic-compatible"
            if paper_url:
                hash_fields["paper_build"] = "custom"
        import_hash = _map_import_hash(
            map_digest=map_digest,
            resource_pack_digest=resource_pack_digest,
            resource_pack_required=(
                resource_pack_import.required if resource_pack_import else False
            ),
            resource_pack_prompt=(
                resource_pack_import.prompt if resource_pack_import else None
            ),
            fields=hash_fields,
            java_major=java_major,
            server_settings=server_settings,
        )
        return await to_thread.run_sync(
            _import_map,
            database,
            maps,
            map_path,
            fields.get("name", ""),
            fields["mc_version"],
            fields["paper_build"],
            java_major,
            fields.get("paper_url") or None,
            fields.get("paper_sha256") or None,
            idempotency_key,
            import_hash,
            resource_pack_import,
            server_settings.model_dump(mode="json", exclude_none=True),
        )

    @app.post("/api/uploads/{upload_id}", status_code=201)
    async def create_chunked_upload(
        upload_id: str,
        request: Request,
    ) -> dict[str, bool | int | str]:
        raw_metadata = bytearray()
        async for part in request.stream():
            raw_metadata.extend(part)
            if len(raw_metadata) > 64 * 1024:
                raise ValidationError(
                    "upload_metadata_too_large", "上传元数据不能超过 64 KiB"
                )
        try:
            payload = cast(object, json.loads(raw_metadata))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError(
                "upload_metadata_invalid", "上传元数据不是有效 JSON"
            ) from error
        if not isinstance(payload, dict):
            raise ValidationError("upload_metadata_invalid", "上传元数据格式无效")
        raw_payload = cast(dict[object, object], payload)
        if not all(isinstance(key, str) for key in raw_payload):
            raise ValidationError("upload_metadata_invalid", "上传元数据格式无效")
        metadata = cast(dict[str, object], raw_payload)
        chunk_size, completed = await to_thread.run_sync(
            chunked_uploads.create, upload_id, metadata
        )
        return {
            "upload_id": chunked_uploads.normalize_id(upload_id),
            "chunk_size": chunk_size,
            "completed": completed,
        }

    @app.put("/api/uploads/{upload_id}/{kind}/{index}", status_code=204)
    async def upload_chunk(
        upload_id: str,
        kind: str,
        index: int,
        request: Request,
        chunk_sha256: Annotated[str, Header(alias="X-Chunk-SHA256")],
    ) -> Response:
        expected_size = await to_thread.run_sync(
            chunked_uploads.expected_chunk_size, upload_id, kind, index
        )
        content_length = request.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError as error:
                raise ValidationError(
                    "chunk_size_invalid", "Content-Length 格式无效"
                ) from error
            if declared_size != expected_size:
                raise ValidationError(
                    "chunk_size_invalid",
                    f"分片 {index} 大小应为 {expected_size} 字节",
                )
        data = bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data) > expected_size:
                raise ValidationError(
                    "chunk_size_invalid",
                    f"分片 {index} 大小应为 {expected_size} 字节",
                )
        await to_thread.run_sync(
            chunked_uploads.write_chunk,
            upload_id,
            kind,
            index,
            bytes(data),
            chunk_sha256,
        )
        return Response(status_code=204)

    @app.post("/api/uploads/{upload_id}/complete", response_model=UploadResult)
    async def complete_chunked_upload(upload_id: str) -> UploadResult:
        normalized_upload_id = chunked_uploads.normalize_id(upload_id)
        with chunked_uploads.completion(upload_id) as completion:
            if completion.result is not None:
                return UploadResult.model_validate(completion.result)
            completed = completion.upload
            if completed is None:
                raise RuntimeError("upload completion has no source or result")
            fields = {
                key: str(value)
                for key, value in completed.metadata.items()
                if key
                in {
                    "name",
                    "mc_version",
                    "paper_build",
                    "paper_url",
                    "paper_sha256",
                    "resource_pack_filename",
                    "resource_pack_required",
                    "resource_pack_prompt",
                    "server_settings",
                }
            }
            record = await import_saved_map(
                completed.map_path,
                completed.resource_pack_path,
                fields,
                f"chunked-upload:{normalized_upload_id}",
            )
            result = UploadResult(
                map_id=record.map_id,
                name=record.name,
                mc_version=record.mc_version,
            )
            await to_thread.run_sync(
                chunked_uploads.finish,
                upload_id,
                result.model_dump(mode="json"),
            )
            return result

    @app.delete("/api/uploads/{upload_id}", status_code=204)
    async def cancel_chunked_upload(upload_id: str) -> Response:
        await to_thread.run_sync(chunked_uploads.cancel, upload_id)
        return Response(status_code=204)

    @app.post("/api/maps", response_model=UploadResult, status_code=201)
    async def upload_map(
        request: Request,
        idempotency_key: IdempotencyKey = None,
    ) -> UploadResult:
        form = await request.form(max_files=256, max_fields=32, max_part_size=1024 * 1024)
        map_upload: UploadFile | None = None
        resource_pack_upload: UploadFile | None = None
        fields: dict[str, str] = {}
        try:
            for key, value in form.multi_items():
                if isinstance(value, UploadFile):
                    if key == "resource_pack":
                        if resource_pack_upload is not None:
                            raise ValidationError(
                                "duplicate_resource_pack", "只能上传一个客户端资源包"
                            )
                        resource_pack_upload = value
                    elif key in {"map", "map.zip"} or value.filename == "map.zip":
                        if map_upload is not None:
                            raise ValidationError("duplicate_map", "只能上传一个 map.zip")
                        map_upload = value
                    else:
                        raise ValidationError(
                            "unexpected_upload", "只允许上传地图和一个玩家资源包"
                        )
                else:
                    fields[key] = str(value)
            if map_upload is None:
                raise ValidationError("map_required", "multipart 请求必须包含 map.zip")
            if (
                resource_pack_upload is not None
                and resolved_settings.resource_pack_base_url is None
            ):
                raise ValidationError(
                    "resource_pack_base_url_missing",
                    "后端未配置资源包公网地址, 暂时不能上传客户端资源包",
                )
        except BaseException:
            await form.close()
            raise

        upload_dir = resolved_settings.upload_root / uuid.uuid4().hex
        try:
            upload_dir.mkdir(parents=True, mode=0o700)
            map_path = upload_dir / "map.zip"
            await _save_upload(
                map_upload, map_path, resolved_settings.max_upload_bytes
            )
            total = map_path.stat().st_size

            resource_pack_path: Path | None = None
            if resource_pack_upload is not None:
                submitted_filename = resource_pack_upload.filename or ""
                filename = Path(submitted_filename.replace("\\", "/")).name
                if not SAFE_RESOURCE_NAME.fullmatch(filename):
                    filename = "resources.zip"
                resource_pack_path = upload_dir / "client-resource-pack.zip"
                remaining = resolved_settings.max_upload_bytes - total
                if remaining <= 0:
                    raise ValidationError("upload_too_large", "上传总大小超过限制")
                await _save_upload(
                    resource_pack_upload,
                    resource_pack_path,
                    min(remaining, resolved_settings.max_resource_pack_bytes),
                )
                fields["resource_pack_filename"] = filename

            record = await import_saved_map(
                map_path, resource_pack_path, fields, idempotency_key
            )
            return UploadResult(
                map_id=record.map_id,
                name=record.name,
                mc_version=record.mc_version,
            )
        finally:
            await to_thread.run_sync(_remove_tree, upload_dir)
            await form.close()

    @app.post("/api/maps/generated", response_model=UploadResult, status_code=201)
    async def generate_map(
        request: Annotated[GenerateMapRequest, Body()],
        idempotency_key: IdempotencyKey = None,
    ) -> UploadResult:
        try:
            java_major = required_java_major(request.mc_version)
        except ValueError as error:
            raise ValidationError("mc_version_unsupported", str(error)) from error
        paper_build = await resolve_paper_build(
            request.mc_version, request.paper_build, request.paper_url
        )
        settings_payload = request.server_settings.model_dump(mode="json", exclude_none=True)
        generated_hash = _generated_map_hash(
            name=request.name,
            mc_version=request.mc_version,
            paper_build=request.paper_build,
            java_major=java_major,
            paper_url=request.paper_url,
            paper_sha256=request.paper_sha256,
            server_settings=settings_payload,
        )
        record = await to_thread.run_sync(
            _create_generated_map,
            database,
            maps,
            request.name,
            request.mc_version,
            paper_build,
            java_major,
            request.paper_url,
            request.paper_sha256,
            idempotency_key,
            generated_hash,
            settings_payload,
        )
        return UploadResult(
            map_id=record.map_id,
            name=record.name,
            mc_version=record.mc_version,
        )

    @app.delete("/api/maps/{map_id}", status_code=204)
    def delete_map(map_id: int, session: SessionDependency) -> Response:
        record = session.get(MapRecord, map_id)
        if record is None:
            raise NotFoundError("map_not_found", "仓库地图不存在")
        game_exists = session.scalar(select(GameRecord.game_id).where(GameRecord.map_id == map_id))
        if game_exists is not None:
            raise ConflictError("map_in_use", "存在由该地图创建的游戏, 不能删除")
        source = storage.resolve(record.relative_path)
        trash = storage.temporary_sibling(source, f"delete-map-{map_id}")
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
        latest_run_id = (
            select(RunRecord.run_id)
            .where(RunRecord.game_id == GameRecord.game_id)
            .order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
            .limit(1)
            .correlate(GameRecord)
            .scalar_subquery()
        )
        rows = (
            session.execute(
                select(GameRecord, RunRecord)
                .outerjoin(RunRecord, RunRecord.run_id == latest_run_id)
                .options(selectinload(GameRecord.backups))
                .order_by(GameRecord.game_id)
            )
            .unique()
            .all()
        )
        return [_game_view(game, run, resolved_settings) for game, run in rows]

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
            server_settings=(
                request.server_settings.model_dump(mode="json", exclude_none=True)
                if request.server_settings is not None
                else None
            ),
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
        run = session.scalar(
            select(RunRecord)
            .where(RunRecord.game_id == game_id)
            .order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
        )
        return _game_view(game, run, resolved_settings)

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
            backup=request.backup,
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
        lease_rows = session.execute(
            select(PortLease, RunRecord.game_id)
            .outerjoin(RunRecord, PortLease.run_id == RunRecord.run_id)
            .order_by(PortLease.port)
        ).all()
        return StatusView(
            running_games=[
                RunningGameView(
                    game_id=run.game_id,
                    game_name=run.game.name,
                    mc_version=run.game.map.mc_version,
                    last_played_at=run.game.last_played_at,
                    observed_state=run.observed_state,
                    port=run.port,
                    public_address=resolved_settings.public_game_address(run.port),
                    last_error=run.last_error,
                )
                for run in runs
            ],
            tasks=[TaskView.model_validate(task) for task in recent_tasks],
            ports=[
                PortView(
                    port=lease.port, state=lease.state, game_id=game_id
                )
                for lease, game_id in lease_rows
            ],
        )

    @app.api_route(
        "/api/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    def unknown_api(full_path: str) -> JSONResponse:
        del full_path
        return _error_response(404, "api_not_found", "API 接口不存在")

    index_file = STATIC_ROOT / "index.html"
    assets_directory = STATIC_ROOT / "assets"
    if index_file.is_file():
        if assets_directory.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_directory), name="frontend-assets")

        @app.get("/", include_in_schema=False)
        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend(full_path: str = "") -> FileResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (STATIC_ROOT / full_path).resolve()
            if candidate.is_relative_to(STATIC_ROOT) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)

    return app


def _game_view(game: GameRecord, run: RunRecord | None, settings: Settings) -> GameView:
    runtime_state = run.observed_state if run is not None else None
    port: int | None = None
    public_address: str | None = None
    if run is not None and run.observed_state in LIVE_STATES:
        port = run.port
        public_address = settings.public_game_address(run.port)
    return GameView(
        game_id=game.game_id,
        map_id=game.map_id,
        map_name=game.map.name,
        mc_version=game.map.mc_version,
        paper_build=game.map.paper_build,
        java_major=game.map.java_major,
        state=game.state,
        name=game.name,
        created_at=game.created_at,
        last_played_at=game.last_played_at,
        runtime_state=runtime_state,
        port=port,
        public_address=public_address,
        backups=[BackupView.model_validate(backup) for backup in game.retained_backups],
        server_settings=ServerSettings.model_validate(game.server_settings),
    )


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


def _save_upload_file(
    source: BinaryIO, destination: Path, limit: int
) -> tuple[str, str]:
    written = 0
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1(usedforsecurity=False)
    source.seek(0)
    with destination.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                raise ValidationError("upload_too_large", "上传内容超过大小限制")
            output.write(chunk)
            sha256.update(chunk)
            sha1.update(chunk)
    return sha256.hexdigest(), sha1.hexdigest()


def _file_digests(path: Path) -> tuple[str, str]:
    with path.open("rb") as source:
        return _hash_stream(source)


def _hash_stream(source: BinaryIO) -> tuple[str, str]:
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1(usedforsecurity=False)
    while chunk := source.read(1024 * 1024):
        sha256.update(chunk)
        sha1.update(chunk)
    return sha256.hexdigest(), sha1.hexdigest()


async def _save_upload(upload: UploadFile, destination: Path, limit: int) -> tuple[str, str]:
    try:
        return await to_thread.run_sync(
            _save_upload_file, upload.file, destination, limit
        )
    finally:
        await upload.close()


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _parse_form_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValidationError("invalid_boolean", f"无效的布尔值: {value}")


def _parse_server_settings(value: str | None) -> ServerSettings:
    if value is None or not value.strip():
        return ServerSettings()
    try:
        payload: object = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValidationError(
            "server_settings_invalid", "服务端设置必须是有效 JSON"
        ) from error
    try:
        return ServerSettings.model_validate(payload)
    except ValueError as error:
        raise ValidationError("server_settings_invalid", str(error)) from error


def _generated_map_hash(
    *,
    name: str,
    mc_version: str,
    paper_build: str | None,
    java_major: int,
    paper_url: str | None,
    paper_sha256: str | None,
    server_settings: dict[str, Any],
) -> str:
    payload = {
        "type": "generated_map",
        "name": name.strip(),
        "mc_version": mc_version.strip(),
        "paper_build": (
            paper_build.strip()
            if paper_build
            else "custom" if paper_url else "automatic-compatible"
        ),
        "java_major": java_major,
        "paper_url": (paper_url or "").strip(),
        "paper_sha256": (paper_sha256 or "").strip().lower(),
        "server_settings": server_settings,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _map_import_hash(
    *,
    map_digest: str,
    resource_pack_digest: tuple[str, str] | None = None,
    resource_pack_required: bool = False,
    resource_pack_prompt: str | None = None,
    fields: dict[str, str],
    java_major: int,
    server_settings: ServerSettings | None = None,
) -> str:
    payload: dict[str, object] = {
        "map": map_digest,
        "resource_pack": resource_pack_digest,
        "resource_pack_required": resource_pack_required,
        "resource_pack_prompt": resource_pack_prompt,
        "name": fields.get("name", "").strip(),
        "mc_version": fields.get("mc_version", "").strip(),
        "paper_build": fields.get("paper_build", "").strip(),
        "java_major": java_major,
        "paper_url": fields.get("paper_url", "").strip(),
        "paper_sha256": fields.get("paper_sha256", "").strip().lower(),
        "server_settings": (server_settings or ServerSettings()).model_dump(
            mode="json", exclude_none=True
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _import_map(
    database: Database,
    maps: MapService,
    map_path: Path,
    name: str,
    mc_version: str,
    paper_build: str,
    java_major: int,
    paper_url: str | None,
    paper_sha256: str | None,
    idempotency_key: str | None,
    import_hash: str,
    resource_pack: ResourcePackImport | None,
    server_settings: dict[str, Any],
) -> MapRecord:
    if idempotency_key:
        lock_digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        lock_path = maps.storage.staging_root / f"map-import-{lock_digest}.lock"
        with lock_path.open("a", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ConflictError(
                    "import_in_progress", "相同地图上传仍在处理中, 请稍后重试"
                ) from error
            return _import_map_locked(
                database,
                maps,
                map_path,
                name,
                mc_version,
                paper_build,
                java_major,
                paper_url,
                paper_sha256,
                idempotency_key,
                import_hash,
                resource_pack,
                server_settings,
            )
    return _import_map_locked(
        database,
        maps,
        map_path,
        name,
        mc_version,
        paper_build,
        java_major,
        paper_url,
        paper_sha256,
        None,
        import_hash,
        resource_pack,
        server_settings,
    )


def _import_map_locked(
    database: Database,
    maps: MapService,
    map_path: Path,
    name: str,
    mc_version: str,
    paper_build: str,
    java_major: int,
    paper_url: str | None,
    paper_sha256: str | None,
    idempotency_key: str | None,
    import_hash: str,
    resource_pack: ResourcePackImport | None,
    server_settings: dict[str, Any],
) -> MapRecord:
    with database.session_factory() as session:
        if idempotency_key:
            existing = session.scalar(
                select(MapRecord).where(
                    MapRecord.import_idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return _existing_map_import(
                    session,
                    maps,
                    existing,
                    import_hash,
                    map_path,
                    resource_pack,
                )
        try:
            record = maps.prepare_import(
                session,
                name=name,
                mc_version=mc_version,
                paper_build=paper_build,
                java_major=java_major,
                paper_url=paper_url,
                paper_sha256=paper_sha256,
                idempotency_key=idempotency_key,
                request_hash=import_hash,
                server_settings=server_settings,
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            if not idempotency_key:
                raise
            existing = session.scalar(
                select(MapRecord).where(
                    MapRecord.import_idempotency_key == idempotency_key
                )
            )
            if existing is None:
                raise
            return _existing_map_import(
                session,
                maps,
                existing,
                import_hash,
                map_path,
                resource_pack,
            )

        try:
            record = maps.publish_import(
                session,
                record,
                archive_path=map_path,
                resource_pack=resource_pack,
            )
            session.commit()
            return record
        except Exception:
            session.rollback()
            failed = session.get(MapRecord, record.map_id)
            if failed is not None:
                failed.state = ResourceState.FAILED
                session.commit()
            raise


def _existing_map_import(
    session: Session,
    maps: MapService,
    record: MapRecord,
    import_hash: str,
    map_path: Path,
    resource_pack: ResourcePackImport | None,
) -> MapRecord:
    if record.import_request_hash != import_hash:
        raise ConflictError(
            "idempotency_key_reused",
            "同一 Idempotency-Key 不能用于不同地图上传",
        )
    if record.state == ResourceState.READY:
        return record
    destination = maps.storage.resolve(record.relative_path)
    if record.state in {ResourceState.PREPARING, ResourceState.FAILED} and destination.is_dir():
        record.content_sha256, _ = maps.storage.tree_digest(destination)
        record.data_version = read_data_version(destination)
        record.extra_metadata = {
            "resource_pack": maps.recover_resource_pack_metadata(
                record.map_id, destination
            ),
        }
        record.state = ResourceState.READY
        session.commit()
        return record
    if record.state in {ResourceState.PREPARING, ResourceState.FAILED}:
        record.state = ResourceState.PREPARING
        session.commit()
        try:
            record = maps.publish_import(
                session,
                record,
                archive_path=map_path,
                resource_pack=resource_pack,
            )
            session.commit()
            return record
        except Exception:
            session.rollback()
            failed = session.get(MapRecord, record.map_id)
            if failed is not None:
                failed.state = ResourceState.FAILED
                session.commit()
            raise
    raise ConflictError("import_failed", "之前的地图导入失败, 请重新选择文件后再试")


def _create_generated_map(
    database: Database,
    maps: MapService,
    name: str,
    mc_version: str,
    paper_build: str,
    java_major: int,
    paper_url: str | None,
    paper_sha256: str | None,
    idempotency_key: str | None,
    request_hash: str,
    server_settings: dict[str, Any],
) -> MapRecord:
    lock_key = idempotency_key or f"generated:{request_hash}"
    lock_digest = hashlib.sha256(lock_key.encode()).hexdigest()
    lock_path = maps.storage.staging_root / f"map-import-{lock_digest}.lock"
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ConflictError(
                "import_in_progress", "相同地图创建仍在处理中, 请稍后重试"
            ) from error
        with database.session_factory() as session:
            existing = None
            if idempotency_key:
                existing = session.scalar(
                    select(MapRecord).where(
                        MapRecord.import_idempotency_key == idempotency_key
                    )
                )
            if existing is not None:
                if existing.import_request_hash != request_hash:
                    raise ConflictError(
                        "idempotency_key_reused",
                        "同一 Idempotency-Key 不能用于不同地图创建",
                    )
                if existing.state == ResourceState.READY:
                    return existing
                destination = maps.storage.resolve(existing.relative_path)
                if destination.is_dir():
                    existing.content_sha256, _ = maps.storage.tree_digest(destination)
                    existing.state = ResourceState.READY
                    session.commit()
                    return existing
                existing.state = ResourceState.PREPARING
                session.commit()
                record = existing
            else:
                record = maps.prepare_import(
                    session,
                    name=name,
                    mc_version=mc_version,
                    paper_build=paper_build,
                    java_major=java_major,
                    paper_url=paper_url,
                    paper_sha256=paper_sha256,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    source_type=MapSourceType.GENERATED,
                    server_settings=server_settings,
                )
                session.commit()
            try:
                record = maps.publish_generated(session, record)
                session.commit()
                return record
            except Exception:
                session.rollback()
                failed = session.get(MapRecord, record.map_id)
                if failed is not None:
                    failed.state = ResourceState.FAILED
                    session.commit()
                raise


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
