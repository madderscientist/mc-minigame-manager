import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from mc_manager.config import Settings
from mc_manager.errors import ValidationError


class SafeZipExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _validate_name(name: str) -> PurePosixPath:
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValidationError("unsafe_archive_path", f"压缩包包含非法路径: {name}")
        if path.parts[0].endswith(":"):
            raise ValidationError("unsafe_archive_path", f"压缩包包含绝对路径: {name}")
        return path

    def extract(self, archive: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(archive) as bundle:
                infos = bundle.infolist()
                if len(infos) > self.settings.max_archive_files:
                    raise ValidationError("too_many_files", "压缩包文件数量超过限制")
                total = 0
                for info in infos:
                    relative = self._validate_name(info.filename)
                    mode = info.external_attr >> 16
                    file_type = stat.S_IFMT(mode)
                    if file_type == stat.S_IFLNK:
                        raise ValidationError("symlink_not_allowed", "压缩包中禁止符号链接")
                    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        raise ValidationError("special_file_not_allowed", "压缩包中禁止特殊文件")
                    if info.file_size > self.settings.max_single_file_bytes:
                        raise ValidationError(
                            "file_too_large", f"文件超过大小限制: {info.filename}"
                        )
                    total += info.file_size
                    if total > self.settings.max_extracted_bytes:
                        raise ValidationError("archive_too_large", "压缩包展开后超过总大小限制")
                    if info.compress_size == 0:
                        ratio = float("inf") if info.file_size else 1.0
                    else:
                        ratio = info.file_size / info.compress_size
                    if ratio > self.settings.max_compression_ratio:
                        raise ValidationError("compression_bomb", "压缩比超过安全限制")

                    target = destination.joinpath(*relative.parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(info) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    @staticmethod
    def validate_map_layout(directory: Path) -> None:
        found_level = any(
            path.name == "level.dat" and path.is_file()
            for path in directory.rglob("level.dat")
        )
        if not found_level:
            raise ValidationError("invalid_map", "地图压缩包中未找到 level.dat")
