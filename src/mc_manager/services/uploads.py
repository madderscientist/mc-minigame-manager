import fcntl
import hashlib
import json
import math
import os
import shutil
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, cast

from mc_manager.errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True, slots=True)
class CompletedUpload:
    upload_id: str
    directory: Path
    map_path: Path
    resource_pack_path: Path | None
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class UploadCompletion:
    upload: CompletedUpload | None
    result: dict[str, object] | None


class ChunkedUploadStore:
    METADATA_DEFAULTS: ClassVar[dict[str, object]] = {
        "resource_pack_size": 0,
        "resource_pack_filename": "",
        "resource_pack_required": False,
        "resource_pack_prompt": "",
        "mc_version": "",
        "paper_build": "",
        "paper_url": "",
        "paper_sha256": "",
    }
    STRING_LIMITS: ClassVar[dict[str, int]] = {
        "name": 255,
        "resource_pack_filename": 255,
        "resource_pack_prompt": 256,
        "mc_version": 32,
        "paper_build": 64,
        "paper_url": 2048,
        "paper_sha256": 64,
    }

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        max_resource_pack_bytes: int,
        max_sessions: int,
        max_reserved_bytes: int,
        chunk_size: int = 8 * 1024**2,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.root = root / "chunked"
        self.lock_root = self.root / ".locks"
        self.max_bytes = max_bytes
        self.max_resource_pack_bytes = max_resource_pack_bytes
        self.max_sessions = max_sessions
        self.max_reserved_bytes = max_reserved_bytes
        self.chunk_size = chunk_size
        self.ttl = ttl

    @staticmethod
    def normalize_id(upload_id: str) -> str:
        try:
            return str(uuid.UUID(upload_id))
        except ValueError as error:
            raise ValidationError("upload_id_invalid", "上传标识格式无效") from error

    def create(
        self, upload_id: str, metadata: Mapping[str, object]
    ) -> tuple[int, bool]:
        self.cleanup_expired()
        normalized = self.normalize_id(upload_id)
        canonical = self._canonical_metadata(normalized, metadata)
        map_size = self._size(canonical, "map_size", required=True)
        resource_pack_size = self._size(
            canonical, "resource_pack_size", required=False
        )
        if map_size + resource_pack_size > self.max_bytes:
            raise ValidationError("upload_too_large", "地图和资源包总大小超过限制")
        if resource_pack_size > self.max_resource_pack_bytes:
            raise ValidationError(
                "resource_pack_too_large", "客户端资源包不能超过 250 MiB"
            )
        directory = self.root / normalized
        with (
            self._lock("global", "quota", fcntl.LOCK_EX, nonblocking=False),
            self._lock(normalized, "create", fcntl.LOCK_EX, nonblocking=False),
            self._lock(normalized, "session", fcntl.LOCK_SH),
        ):
            if directory.is_dir():
                if self._read_metadata(directory) != canonical:
                    raise ConflictError(
                        "upload_id_reused", "上传标识已用于其他文件"
                    )
                directory.touch()
                return self.chunk_size, (directory / "result.json").is_file()

            self._check_capacity(map_size + resource_pack_size)
            temporary = self.root / f".{normalized}-{uuid.uuid4().hex}.tmp"
            try:
                temporary.mkdir(mode=0o700)
                self._write_json(temporary / "metadata.json", canonical)
                self._prepare_file(temporary / "map.part", map_size)
                if resource_pack_size:
                    self._prepare_file(
                        temporary / "resource_pack.part", resource_pack_size
                    )
                (temporary / "chunks").mkdir(mode=0o700)
                os.replace(temporary, directory)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        return self.chunk_size, False

    def expected_chunk_size(self, upload_id: str, kind: str, index: int) -> int:
        normalized = self.normalize_id(upload_id)
        with self._lock(normalized, "session", fcntl.LOCK_SH):
            directory = self.directory(normalized)
            metadata = self._read_metadata(directory)
            total_size, _ = self._target(directory, metadata, kind)
            return self._chunk_size(total_size, index)

    def write_chunk(
        self,
        upload_id: str,
        kind: str,
        index: int,
        data: bytes,
        expected_sha256: str,
    ) -> None:
        normalized = self.normalize_id(upload_id)
        with self._lock(normalized, "session", fcntl.LOCK_SH):
            directory = self.directory(normalized)
            metadata = self._read_metadata(directory)
            total_size, target = self._target(directory, metadata, kind)
            expected_size = self._chunk_size(total_size, index)
            if len(data) != expected_size:
                raise ValidationError(
                    "chunk_size_invalid",
                    f"分片 {index} 大小应为 {expected_size} 字节",
                )
            expected_sha256 = expected_sha256.strip().lower()
            if len(expected_sha256) != 64 or any(
                character not in "0123456789abcdef" for character in expected_sha256
            ):
                raise ValidationError("chunk_checksum_invalid", "分片校验值格式无效")
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValidationError(
                    "chunk_checksum_mismatch", f"分片 {index} 校验失败"
                )

            marker = directory / "chunks" / f"{kind}-{index}"
            with self._chunk_lock(directory, kind, index):
                if marker.exists():
                    if marker.read_text(encoding="ascii") != actual_sha256:
                        raise ConflictError(
                            "chunk_conflict", f"分片 {index} 内容冲突"
                        )
                    directory.touch()
                    return

                descriptor = os.open(target, os.O_WRONLY)
                try:
                    offset = index * self.chunk_size
                    written = 0
                    while written < len(data):
                        count = os.pwrite(
                            descriptor, data[written:], offset + written
                        )
                        if count <= 0:
                            raise OSError("unable to write upload chunk")
                        written += count
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self._write_text(marker, actual_sha256)
                directory.touch()

    @contextmanager
    def completion(self, upload_id: str) -> Generator[UploadCompletion, None, None]:
        normalized = self.normalize_id(upload_id)
        with self._lock(normalized, "session", fcntl.LOCK_EX):
            directory = self.directory(normalized)
            result = self._read_result(directory)
            if result is not None:
                yield UploadCompletion(upload=None, result=result)
                return
            metadata = self._read_metadata(directory)
            self._require_chunks(
                directory, "map", self._size(metadata, "map_size", True)
            )
            resource_size = self._size(metadata, "resource_pack_size", False)
            if resource_size:
                self._require_chunks(directory, "resource_pack", resource_size)
            yield UploadCompletion(
                upload=CompletedUpload(
                    upload_id=normalized,
                    directory=directory,
                    map_path=directory / "map.part",
                    resource_pack_path=(
                        directory / "resource_pack.part" if resource_size else None
                    ),
                    metadata=metadata,
                ),
                result=None,
            )

    @staticmethod
    def _read_result(directory: Path) -> dict[str, object] | None:
        path = directory / "result.json"
        if not path.is_file():
            return None
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValidationError("upload_result_invalid", "上传结果损坏")
        return ChunkedUploadStore._validated_object_dict(
            cast(dict[object, object], payload),
            "upload_result_invalid",
            "上传结果损坏",
        )

    def finish(self, upload_id: str, result: Mapping[str, object]) -> None:
        directory = self.directory(upload_id)
        self._write_json(directory / "result.json", dict(result))
        for name in ("map.part", "resource_pack.part"):
            (directory / name).unlink(missing_ok=True)
        shutil.rmtree(directory / "chunks", ignore_errors=True)

    def cancel(self, upload_id: str) -> None:
        normalized = self.normalize_id(upload_id)
        with (
            self._lock("global", "quota", fcntl.LOCK_EX, nonblocking=False),
            self._lock(normalized, "create", fcntl.LOCK_EX, nonblocking=False),
            self._lock(normalized, "session", fcntl.LOCK_EX),
        ):
            directory = self.root / normalized
            if (directory / "result.json").is_file():
                return
            shutil.rmtree(directory, ignore_errors=True)

    def cleanup_expired(self, now: datetime | None = None) -> int:
        if not self.root.is_dir():
            return 0
        with self._lock("global", "quota", fcntl.LOCK_EX, nonblocking=False):
            return self._cleanup_expired_locked(now)

    def _cleanup_expired_locked(self, now: datetime | None) -> int:
        threshold = (now or datetime.now(UTC)) - self.ttl
        removed = 0
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.name == ".locks":
                continue
            try:
                modified = datetime.fromtimestamp(directory.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if modified >= threshold:
                continue
            if directory.name.startswith(".") and directory.name.endswith(".tmp"):
                shutil.rmtree(directory, ignore_errors=True)
                removed += int(not directory.exists())
                continue
            try:
                normalized = self.normalize_id(directory.name)
            except ValidationError:
                continue
            try:
                with self._lock(normalized, "session", fcntl.LOCK_EX):
                    if directory.exists():
                        shutil.rmtree(directory)
                        removed += 1
            except (ConflictError, OSError):
                continue
        return removed

    def _check_capacity(self, additional_bytes: int) -> None:
        active_sessions = 0
        reserved_bytes = 0
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            map_part = directory / "map.part"
            try:
                if not map_part.is_file():
                    continue
                session_bytes = map_part.stat().st_size
                resource_part = directory / "resource_pack.part"
                if resource_part.is_file():
                    session_bytes += resource_part.stat().st_size
            except FileNotFoundError:
                # A successful completion may release part files while quota is scanned.
                continue
            active_sessions += 1
            reserved_bytes += session_bytes
        if active_sessions >= self.max_sessions:
            raise ConflictError(
                "upload_capacity_reached", "并发上传会话已达上限, 请稍后重试"
            )
        if reserved_bytes + additional_bytes > self.max_reserved_bytes:
            raise ConflictError(
                "upload_capacity_reached", "上传预留空间不足, 请稍后重试"
            )

    def directory(self, upload_id: str) -> Path:
        directory = self.root / self.normalize_id(upload_id)
        if not directory.is_dir():
            raise NotFoundError("upload_not_found", "上传会话不存在或已过期")
        return directory

    def _canonical_metadata(
        self, upload_id: str, metadata: Mapping[str, object]
    ) -> dict[str, object]:
        allowed = {"map_size", *self.METADATA_DEFAULTS, "name"}
        unexpected = sorted(set(metadata) - allowed)
        if unexpected:
            raise ValidationError(
                "upload_metadata_invalid", f"不支持的上传字段: {unexpected[0]}"
            )
        canonical = {**self.METADATA_DEFAULTS, **metadata}
        for key, limit in self.STRING_LIMITS.items():
            value = canonical.get(key, "")
            if not isinstance(value, str) or len(value) > limit:
                raise ValidationError(
                    "upload_metadata_invalid", f"{key} 格式或长度无效"
                )
        if not str(canonical.get("name", "")).strip():
            raise ValidationError("name_required", "地图名称不能为空")
        required_pack = canonical.get("resource_pack_required")
        if not isinstance(required_pack, bool):
            raise ValidationError(
                "upload_metadata_invalid", "resource_pack_required 必须是布尔值"
            )
        self._size(canonical, "map_size", required=True)
        self._size(canonical, "resource_pack_size", required=False)
        return {
            **canonical,
            "upload_id": upload_id,
            "chunk_size": self.chunk_size,
        }

    @staticmethod
    def _size(metadata: Mapping[str, object], key: str, required: bool) -> int:
        value = metadata.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < int(required):
            raise ValidationError("upload_size_invalid", f"{key} 必须是有效字节数")
        return value

    def _target(
        self, directory: Path, metadata: Mapping[str, object], kind: str
    ) -> tuple[int, Path]:
        if kind == "map":
            return self._size(metadata, "map_size", True), directory / "map.part"
        if kind == "resource_pack":
            size = self._size(metadata, "resource_pack_size", False)
            if size:
                return size, directory / "resource_pack.part"
        raise ValidationError("chunk_kind_invalid", "分片文件类型无效")

    def _chunk_size(self, total_size: int, index: int) -> int:
        chunk_count = math.ceil(total_size / self.chunk_size)
        if index < 0 or index >= chunk_count:
            raise ValidationError("chunk_index_invalid", "分片序号超出范围")
        return min(self.chunk_size, total_size - index * self.chunk_size)

    def _require_chunks(self, directory: Path, kind: str, total_size: int) -> None:
        missing = [
            index
            for index in range(math.ceil(total_size / self.chunk_size))
            if not (directory / "chunks" / f"{kind}-{index}").is_file()
        ]
        if missing:
            raise ConflictError(
                "upload_incomplete",
                f"上传仍缺少 {len(missing)} 个分片",
                details={"missing": missing[:100]},
            )

    @staticmethod
    def _prepare_file(path: Path, size: int) -> None:
        with path.open("xb") as output:
            output.truncate(size)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="ascii")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _write_json(cls, path: Path, payload: Mapping[str, object]) -> None:
        cls._write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _read_metadata(directory: Path) -> dict[str, object]:
        try:
            payload: Any = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError("upload_metadata_invalid", "上传会话元数据损坏") from error
        if not isinstance(payload, dict):
            raise ValidationError("upload_metadata_invalid", "上传会话元数据格式无效")
        return ChunkedUploadStore._validated_object_dict(
            cast(dict[object, object], payload),
            "upload_metadata_invalid",
            "上传会话元数据格式无效",
        )

    @staticmethod
    def _validated_object_dict(
        payload: dict[object, object], code: str, message: str
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                raise ValidationError(code, message)
            result[key] = value
        return result

    @contextmanager
    def _lock(
        self,
        upload_id: str,
        scope: str,
        operation: int,
        *,
        nonblocking: bool = True,
    ) -> Generator[None, None, None]:
        self.lock_root.mkdir(parents=True, exist_ok=True)
        stripe = (
            "global"
            if upload_id == "global"
            else hashlib.sha256(upload_id.encode()).hexdigest()[:2]
        )
        lock_path = self.lock_root / f"{scope}-{stripe}.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            flags = operation | (fcntl.LOCK_NB if nonblocking else 0)
            try:
                fcntl.flock(lock, flags)
            except BlockingIOError as error:
                raise ConflictError(
                    "upload_completing", "上传正在完成或取消, 请稍后重试"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    @contextmanager
    def _chunk_lock(
        directory: Path, kind: str, index: int
    ) -> Generator[None, None, None]:
        lock_path = directory / "chunks" / f".{kind}-{index}.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
