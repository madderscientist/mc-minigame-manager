import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from mc_manager.enums import ResourceState
from mc_manager.errors import ValidationError
from mc_manager.models import MapRecord
from mc_manager.services.archive import SafeZipExtractor
from mc_manager.services.server_properties import (
    PAPER_PERMISSION_PROPERTIES,
    update_server_properties,
)
from mc_manager.services.storage import Storage
from mc_manager.services.versions import read_data_version, required_java_major

SAFE_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESOURCE_PACK_STORAGE_NAME = "resource-pack.zip"
RESOURCE_PACK_METADATA_NAME = "metadata.json"
RESOURCE_PACK_PROPERTIES = {
    "resource-pack",
    "resource-pack-sha1",
    "resource-pack-id",
    "resource-pack-prompt",
    "require-resource-pack",
}


@dataclass(frozen=True, slots=True)
class ResourcePackImport:
    path: Path
    filename: str
    sha1: str
    sha256: str
    size_bytes: int
    required: bool
    prompt: str | None


class MapService:
    def __init__(
        self,
        storage: Storage,
        extractor: SafeZipExtractor,
        resource_pack_base_url: str | None = None,
    ) -> None:
        self.storage = storage
        self.extractor = extractor
        self.resource_pack_base_url = resource_pack_base_url

    @staticmethod
    def latest_reusable_paper_build(session: Session, mc_version: str) -> str | None:
        builds = session.scalars(
            select(MapRecord.paper_build).where(
                MapRecord.state == ResourceState.READY,
                MapRecord.mc_version == mc_version.strip(),
                MapRecord.paper_url.is_(None),
            )
        ).all()
        numeric_builds = [int(build) for build in builds if build.isdigit()]
        return str(max(numeric_builds)) if numeric_builds else None

    def prepare_import(
        self,
        session: Session,
        *,
        name: str,
        mc_version: str,
        paper_build: str,
        java_major: int,
        paper_url: str | None,
        paper_sha256: str | None,
        idempotency_key: str | None,
        request_hash: str,
    ) -> MapRecord:
        if not name.strip():
            raise ValidationError("name_required", "地图名称不能为空")
        if not mc_version.strip() or not paper_build.strip():
            raise ValidationError("runtime_metadata_required", "必须指定 MC 版本和 Paper build")
        try:
            expected_java_major = required_java_major(mc_version)
        except ValueError as error:
            raise ValidationError("mc_version_unsupported", str(error)) from error
        if java_major != expected_java_major:
            raise ValidationError(
                "java_version_mismatch",
                f"Minecraft {mc_version.strip()} 必须使用 Java {expected_java_major}",
            )
        if paper_sha256 is not None and (
            len(paper_sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in paper_sha256)
        ):
            raise ValidationError("paper_sha256_invalid", "Paper SHA-256 格式错误")
        if bool(paper_url) != bool(paper_sha256):
            raise ValidationError(
                "paper_artifact_incomplete", "paper_url 和 paper_sha256 必须同时提供"
            )

        record = MapRecord(
            state=ResourceState.PREPARING,
            name=name.strip(),
            mc_version=mc_version.strip(),
            paper_build=paper_build.strip(),
            java_major=java_major,
            paper_url=paper_url,
            paper_sha256=paper_sha256.lower() if paper_sha256 else None,
            relative_path="pending",
            import_idempotency_key=idempotency_key,
            import_request_hash=request_hash,
            extra_metadata={},
        )
        session.add(record)
        session.flush()
        record.relative_path = f"maps/{record.map_id}"
        return record

    def publish_import(
        self,
        session: Session,
        record: MapRecord,
        *,
        archive_path: Path,
        resource_pack: ResourcePackImport | None = None,
    ) -> MapRecord:
        destination = self.storage.resolve(record.relative_path)
        staging = self.storage.staging_path(f"upload-map-{record.map_id}")
        try:
            self.extractor.extract(archive_path, staging)
            self._unwrap_single_root(staging)
            self.extractor.validate_map_layout(staging)
            bundled_resource_pack = staging / "resources.zip"
            had_bundled_resource_pack = bundled_resource_pack.is_file()
            self._normalize_server_root(staging)
            if had_bundled_resource_pack and not bundled_resource_pack.is_file():
                bundled_resource_pack = staging / "world" / "resources.zip"
            selected_resource_pack = resource_pack
            if selected_resource_pack is None and bundled_resource_pack.is_file():
                selected_resource_pack = self._resource_pack_import(bundled_resource_pack)
            record.data_version = read_data_version(staging)
            resource_pack_metadata = self._install_resource_pack(
                staging, record.map_id, selected_resource_pack
            )
            if bundled_resource_pack.is_file():
                bundled_resource_pack.unlink()
            digest, _ = self.storage.tree_digest(staging)
            record.content_sha256 = digest
            record.extra_metadata = {
                "resource_pack": resource_pack_metadata,
            }
            self.storage.copy_tree_atomic(
                staging,
                destination,
                prefix=f"publish-map-{record.map_id}",
            )
            shutil.rmtree(staging, ignore_errors=True)
            record.state = ResourceState.READY
            session.flush()
            return record
        except Exception:
            record.state = ResourceState.FAILED
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(destination, ignore_errors=True)
            raise

    @staticmethod
    def _unwrap_single_root(staging: Path) -> None:
        children = list(staging.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            return
        wrapper = children[0]
        if not any(item.name == "level.dat" for item in wrapper.rglob("level.dat")):
            return
        temporary = staging.with_name(f"{staging.name}-unwrap")
        wrapper.rename(temporary)
        staging.rmdir()
        temporary.rename(staging)

    @staticmethod
    def _normalize_server_root(staging: Path) -> None:
        root_level = staging / "level.dat"
        if root_level.is_file():
            world = staging / "world"
            children = list(staging.iterdir())
            world.mkdir()
            for child in children:
                child.rename(world / child.name)
            level_name = "world"
        else:
            level_files = sorted(
                staging.rglob("level.dat"),
                key=lambda path: (len(path.parts), path.as_posix()),
            )
            if not level_files:
                raise ValidationError("invalid_map", "地图中未找到可启动世界")
            relative_parent = level_files[0].parent.relative_to(staging)
            if len(relative_parent.parts) != 1:
                raise ValidationError(
                    "ambiguous_world_root", "level.dat 必须位于地图根目录或一级世界目录"
                )
            level_name = relative_parent.as_posix()

        update_server_properties(
            staging / "server.properties",
            {
                **PAPER_PERMISSION_PROPERTIES,
                "level-name": level_name,
                "server-port": "25565",
            },
            remove=RESOURCE_PACK_PROPERTIES,
        )

    def _install_resource_pack(
        self,
        staging: Path,
        map_id: int,
        resource_pack: ResourcePackImport | None,
    ) -> dict[str, object] | None:
        if resource_pack is None:
            return None
        metadata = self.describe_resource_pack(map_id, resource_pack)
        resource_directory = staging / ".mc-manager-resources"
        resource_directory.mkdir(mode=0o750, exist_ok=True)
        target = resource_directory / RESOURCE_PACK_STORAGE_NAME
        shutil.copy2(resource_pack.path, target)
        updates = {
            "resource-pack": str(metadata["url"]),
            "resource-pack-sha1": resource_pack.sha1,
            "require-resource-pack": str(resource_pack.required).lower(),
        }
        if resource_pack.prompt:
            updates["resource-pack-prompt"] = (
                json.dumps(
                    {"text": resource_pack.prompt},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).replace("\\", "\\\\")
            )
        update_server_properties(staging / "server.properties", updates)
        (resource_directory / RESOURCE_PACK_METADATA_NAME).write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return metadata

    def recover_resource_pack_metadata(
        self, map_id: int, map_directory: Path
    ) -> dict[str, object] | None:
        resource_directory = map_directory / ".mc-manager-resources"
        resource_pack = resource_directory / RESOURCE_PACK_STORAGE_NAME
        metadata_path = resource_directory / RESOURCE_PACK_METADATA_NAME
        if not resource_pack.exists() and not metadata_path.exists():
            return None
        if not resource_pack.is_file() or not metadata_path.is_file():
            raise ValidationError(
                "resource_pack_metadata_missing", "已发布地图的资源包元数据不完整"
            )
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError(
                "resource_pack_metadata_invalid", "已发布地图的资源包元数据损坏"
            ) from error
        if not isinstance(payload, dict):
            raise ValidationError(
                "resource_pack_metadata_invalid", "已发布地图的资源包元数据格式无效"
            )
        filename = payload.get("filename")
        required = payload.get("required")
        prompt = payload.get("prompt")
        url = payload.get("url")
        parsed_url = urlparse(url) if isinstance(url, str) else None
        if (
            not isinstance(filename, str)
            or not SAFE_RESOURCE_NAME.fullmatch(filename)
            or not isinstance(required, bool)
            or (prompt is not None and not isinstance(prompt, str))
            or not isinstance(url, str)
            or parsed_url is None
            or parsed_url.scheme not in {"http", "https"}
        ):
            raise ValidationError(
                "resource_pack_metadata_invalid", "已发布地图的资源包元数据字段无效"
            )
        recovered = self._resource_pack_import(resource_pack)
        expected_url_suffix = (
            f"/resource-packs/maps/{map_id}/{recovered.sha1}/{quote(filename)}"
        )
        if not parsed_url.path.endswith(expected_url_suffix):
            raise ValidationError(
                "resource_pack_metadata_mismatch",
                "已发布地图的资源包下载地址与文件摘要不一致",
            )
        metadata: dict[str, object] = {
            "filename": filename,
            "sha1": recovered.sha1,
            "sha256": recovered.sha256,
            "size_bytes": recovered.size_bytes,
            "pack_format": self.extractor.validate_resource_pack(resource_pack),
            "required": required,
            "prompt": prompt,
            "url": url,
        }
        for key in ("sha1", "sha256", "size_bytes", "pack_format"):
            if payload.get(key) != metadata[key]:
                raise ValidationError(
                    "resource_pack_metadata_mismatch",
                    "已发布地图的资源包与恢复元数据不一致",
                )
        properties: dict[str, str] = {}
        properties_path = map_directory / "server.properties"
        for line in properties_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                properties[key.strip()] = value.strip()
        expected_prompt = (
            json.dumps(
                {"text": prompt},
                ensure_ascii=False,
                separators=(",", ":"),
            ).replace("\\", "\\\\")
            if prompt
            else None
        )
        if (
            properties.get("resource-pack") != url
            or properties.get("resource-pack-sha1") != recovered.sha1
            or properties.get("require-resource-pack") != str(required).lower()
            or properties.get("resource-pack-prompt") != expected_prompt
        ):
            raise ValidationError(
                "resource_pack_properties_mismatch",
                "已发布地图的资源包配置与恢复元数据不一致",
            )
        return metadata

    @staticmethod
    def _resource_pack_import(path: Path) -> ResourcePackImport:
        sha1 = hashlib.sha1(usedforsecurity=False)
        sha256 = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                sha1.update(chunk)
                sha256.update(chunk)
        return ResourcePackImport(
            path=path,
            filename="resources.zip",
            sha1=sha1.hexdigest(),
            sha256=sha256.hexdigest(),
            size_bytes=path.stat().st_size,
            required=False,
            prompt=None,
        )

    def describe_resource_pack(
        self, map_id: int, resource_pack: ResourcePackImport
    ) -> dict[str, object]:
        if self.resource_pack_base_url is None:
            raise ValidationError(
                "resource_pack_base_url_missing",
                "后端未配置资源包公网地址, 暂时不能上传客户端资源包",
            )
        if not SAFE_RESOURCE_NAME.fullmatch(resource_pack.filename):
            raise ValidationError(
                "resource_pack_name_invalid", "客户端资源包文件名不安全"
            )
        pack_format = self.extractor.validate_resource_pack(resource_pack.path)
        public_url = (
            f"{self.resource_pack_base_url}/resource-packs/maps/{map_id}/"
            f"{resource_pack.sha1}/{quote(resource_pack.filename)}"
        )
        return {
            "filename": resource_pack.filename,
            "sha1": resource_pack.sha1,
            "sha256": resource_pack.sha256,
            "size_bytes": resource_pack.size_bytes,
            "pack_format": pack_format,
            "required": resource_pack.required,
            "prompt": resource_pack.prompt,
            "url": public_url,
        }
