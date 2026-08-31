import os
import re
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from mc_manager.enums import ResourceState
from mc_manager.errors import ValidationError
from mc_manager.models import MapRecord
from mc_manager.services.archive import SafeZipExtractor
from mc_manager.services.storage import Storage
from mc_manager.services.versions import read_data_version

SAFE_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MapService:
    def __init__(self, storage: Storage, extractor: SafeZipExtractor) -> None:
        self.storage = storage
        self.extractor = extractor

    def import_repository(
        self,
        session: Session,
        *,
        archive_path: Path,
        resource_paths: list[Path],
        name: str,
        mc_version: str,
        paper_build: str,
        java_major: int,
        paper_url: str | None,
        paper_sha256: str | None,
    ) -> MapRecord:
        if not name.strip():
            raise ValidationError("name_required", "地图名称不能为空")
        if not mc_version.strip() or not paper_build.strip():
            raise ValidationError("runtime_metadata_required", "必须指定 MC 版本和 Paper build")
        if java_major not in {8, 11, 16, 17, 21}:
            raise ValidationError("java_unsupported", "Java 主版本不在允许列表中")
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
            extra_metadata={},
        )
        session.add(record)
        session.flush()
        record.relative_path = f"maps/{record.map_id}"
        destination = self.storage.resolve(record.relative_path)
        staging = self.storage.staging_path(f"upload-map-{record.map_id}")
        try:
            self.extractor.extract(archive_path, staging)
            self._unwrap_single_root(staging)
            self.extractor.validate_map_layout(staging)
            self._normalize_server_root(staging)
            record.data_version = read_data_version(staging)
            resource_names = self._install_resources(staging, resource_paths)
            digest, _ = self.storage.tree_digest(staging)
            record.content_sha256 = digest
            record.extra_metadata = {"resources": resource_names}
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, destination)
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
        properties["level-name"] = level_name
        properties["server-port"] = "25565"
        properties_path.write_text(
            "# Managed by mc-minigame-manager\n"
            + "".join(f"{key}={value}\n" for key, value in sorted(properties.items())),
            encoding="utf-8",
        )

    @staticmethod
    def _install_resources(staging: Path, resource_paths: list[Path]) -> list[str]:
        if not resource_paths:
            return []
        resource_dir = staging / ".mc-manager-resources"
        resource_dir.mkdir(mode=0o750)
        installed: list[str] = []
        for source in resource_paths:
            name = source.name
            if not SAFE_RESOURCE_NAME.fullmatch(name):
                raise ValidationError("resource_name_invalid", f"资源文件名不安全: {name}")
            target = resource_dir / name
            if target.exists():
                raise ValidationError("resource_duplicate", f"资源文件重名: {name}")
            shutil.copy2(source, target)
            installed.append(name)
        return installed
