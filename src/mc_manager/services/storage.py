import hashlib
import os
import shutil
import uuid
from pathlib import Path

from mc_manager.errors import ValidationError


class Storage:
    def __init__(self, root: Path, staging_root: Path) -> None:
        self.root = root.resolve()
        self.staging_root = staging_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValidationError("unsafe_path", "存储路径超出数据根目录")
        return candidate

    def relative(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValidationError("unsafe_path", "存储路径超出数据根目录")
        return resolved.relative_to(self.root).as_posix()

    def staging_path(self, prefix: str) -> Path:
        return self.staging_root / f"{prefix}-{uuid.uuid4().hex}"

    @staticmethod
    def temporary_sibling(path: Path, prefix: str) -> Path:
        return path.parent / f".{prefix}-{uuid.uuid4().hex}.tmp"

    @staticmethod
    def assert_safe_tree(source: Path) -> None:
        if not source.is_dir() or source.is_symlink():
            raise ValidationError("invalid_map_tree", "地图目录不存在或不是普通目录")
        for item in source.rglob("*"):
            if item.is_symlink():
                raise ValidationError(
                    "symlink_not_allowed", f"地图中禁止符号链接: {item.name}"
                )
            if not item.is_file() and not item.is_dir():
                raise ValidationError(
                    "special_file_not_allowed", f"地图中包含特殊文件: {item.name}"
                )

    def copy_tree_atomic(self, source: Path, destination: Path, *, prefix: str) -> None:
        self.assert_safe_tree(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValidationError("destination_exists", "目标地图目录已存在")
        staging = self.temporary_sibling(destination, prefix)
        try:
            self._copy_tree_content(source, staging)
            self.assert_safe_tree(staging)
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def replace_tree_atomic(self, source: Path, destination: Path, *, prefix: str) -> None:
        self.assert_safe_tree(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = self.temporary_sibling(destination, f"{prefix}-new")
        rollback = self.temporary_sibling(destination, f"{prefix}-old")
        try:
            self._copy_tree_content(source, staging)
            self.assert_safe_tree(staging)
            if destination.exists():
                os.replace(destination, rollback)
            os.replace(staging, destination)
            if rollback.exists():
                shutil.rmtree(rollback)
        except Exception:
            if not destination.exists() and rollback.exists():
                os.replace(rollback, destination)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if rollback.exists() and destination.exists():
                shutil.rmtree(rollback, ignore_errors=True)

    @staticmethod
    def _copy_tree_content(source: Path, destination: Path) -> None:
        """Copy a validated tree without untrusted modes, timestamps, or xattrs."""
        destination.mkdir(mode=0o770)
        for item in source.rglob("*"):
            target = destination / item.relative_to(source)
            if item.is_dir():
                target.mkdir(mode=0o770, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
                shutil.copyfile(item, target)

    @staticmethod
    def tree_digest(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        for item in sorted(path.rglob("*"), key=lambda entry: entry.relative_to(path).as_posix()):
            if item.is_symlink():
                raise ValidationError("symlink_not_allowed", "存储树中禁止符号链接")
            relative = item.relative_to(path).as_posix().encode()
            if item.is_dir():
                digest.update(b"D")
                digest.update(len(relative).to_bytes(4, "big"))
                digest.update(relative)
                continue
            if not item.is_file():
                raise ValidationError("special_file_not_allowed", "存储树中禁止特殊文件")
            file_size = item.stat().st_size
            digest.update(b"F")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(file_size.to_bytes(8, "big"))
            with item.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def legacy_tree_digest(path: Path) -> tuple[str, int]:
        """Read pre-0.2 tree hashes so existing backups can be upgraded on restore."""
        digest = hashlib.sha256()
        size = 0
        for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
            if file_path.is_symlink():
                raise ValidationError("symlink_not_allowed", "存储树中禁止符号链接")
            relative = file_path.relative_to(path).as_posix().encode()
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with file_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        return digest.hexdigest(), size
