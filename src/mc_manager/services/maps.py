import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from mc_manager.enums import ResourceState
from mc_manager.errors import ValidationError
from mc_manager.models import MapRecord
from mc_manager.services.archive import SafeZipExtractor
from mc_manager.services.storage import Storage
from mc_manager.services.versions import read_data_version, required_java_major

SAFE_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESOURCE_PACK_STORAGE_NAME = "resource-pack.zip"
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
            level_files = sorted(staging.rglob("level.dat"), key=lambda path: len(path.parts))
            if not level_files:
                raise ValidationError("invalid_map", "地图中未找到可启动世界")
            relative_parent = level_files[0].parent.relative_to(staging)
            if len(relative_parent.parts) != 1:
                raise ValidationError(
                    "ambiguous_world_root", "level.dat 必须位于地图根目录或一级世界目录"
                )
            level_name = relative_parent.as_posix()

        properties_path = staging / "server.properties"
        properties: dict[str, str] = {}
        if properties_path.exists():
            for line in properties_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line and not line.lstrip().startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    properties[key.strip()] = value.strip()
        for key in RESOURCE_PACK_PROPERTIES:
            properties.pop(key, None)
        properties["level-name"] = level_name
        properties["server-port"] = "25565"
        properties_path.write_text(
            "# Managed by mc-minigame-manager\n"
            + "".join(f"{key}={value}\n" for key, value in sorted(properties.items())),
            encoding="utf-8",
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
        properties_path = staging / "server.properties"
        properties: dict[str, str] = {}
        for line in properties_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                properties[key.strip()] = value.strip()
        properties["resource-pack"] = str(metadata["url"])
        properties["resource-pack-sha1"] = resource_pack.sha1
        properties["require-resource-pack"] = str(resource_pack.required).lower()
        if resource_pack.prompt:
            properties["resource-pack-prompt"] = (
                json.dumps(
                    {"text": resource_pack.prompt},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).replace("\\", "\\\\")
            )
        properties_path.write_text(
            "# Managed by mc-minigame-manager\n"
            + "".join(f"{key}={value}\n" for key, value in sorted(properties.items())),
            encoding="utf-8",
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
