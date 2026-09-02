import json
import shutil
import stat
import unicodedata
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
        if any(unicodedata.category(character) == "Cc" for character in normalized):
            raise ValidationError("unsafe_archive_path", "压缩包路径不能包含控制字符")
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
                total = sum(info.file_size for info in infos)
                compressed_total = sum(info.compress_size for info in infos)
                if total > self.settings.max_extracted_bytes:
                    raise ValidationError("archive_too_large", "压缩包展开后超过总大小限制")
                ratio = total / max(compressed_total, 1)
                if ratio > self.settings.max_compression_ratio:
                    raise ValidationError(
                        "compression_bomb",
                        f"ZIP 总压缩比 {ratio:.1f} 超过安全限制 "
                        f"{self.settings.max_compression_ratio:g}",
                    )
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

    def validate_resource_pack(self, archive: Path) -> int:
        if archive.stat().st_size > self.settings.max_resource_pack_bytes:
            raise ValidationError(
                "resource_pack_too_large", "客户端资源包不能超过 250 MiB"
            )
        try:
            bundle = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as error:
            raise ValidationError(
                "resource_pack_invalid_zip", "客户端资源包不是有效的 ZIP"
            ) from error
        with bundle:
            infos = bundle.infolist()
            if len(infos) > self.settings.max_archive_files:
                raise ValidationError("too_many_files", "资源包文件数量超过限制")
            metadata_entries: list[zipfile.ZipInfo] = []
            total = 0
            compressed_total = 0
            for info in infos:
                relative = self._validate_name(info.filename)
                if info.flag_bits & 0x1:
                    raise ValidationError(
                        "encrypted_resource_pack", "客户端资源包不能包含加密文件"
                    )
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type == stat.S_IFLNK:
                    raise ValidationError("symlink_not_allowed", "资源包中禁止符号链接")
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValidationError("special_file_not_allowed", "资源包中禁止特殊文件")
                total += info.file_size
                compressed_total += info.compress_size
                if total > self.settings.max_extracted_bytes:
                    raise ValidationError("resource_pack_expanded_too_large", "资源包展开后过大")
                if relative.parts == ("pack.mcmeta",):
                    metadata_entries.append(info)

            ratio = total / max(compressed_total, 1)
            if ratio > self.settings.max_compression_ratio:
                raise ValidationError(
                    "compression_bomb",
                    f"资源包 ZIP 总压缩比 {ratio:.1f} 超过安全限制 "
                    f"{self.settings.max_compression_ratio:g}",
                )

            if len(metadata_entries) != 1:
                raise ValidationError(
                    "resource_pack_metadata_invalid",
                    "资源包 ZIP 根目录必须且只能包含一个 pack.mcmeta",
                )
            metadata_info = metadata_entries[0]
            if metadata_info.file_size > 1024 * 1024:
                raise ValidationError("resource_pack_metadata_too_large", "pack.mcmeta 过大")
            try:
                metadata = json.loads(bundle.read(metadata_info).decode("utf-8"))
                pack_format = metadata["pack"]["pack_format"]
            except (
                KeyError,
                TypeError,
                RuntimeError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                zipfile.BadZipFile,
            ) as error:
                raise ValidationError(
                    "resource_pack_metadata_invalid", "pack.mcmeta 不是有效的资源包元数据"
                ) from error
            if (
                not isinstance(pack_format, int)
                or isinstance(pack_format, bool)
                or pack_format < 1
            ):
                raise ValidationError(
                    "resource_pack_metadata_invalid", "pack.pack_format 必须是整数"
                )
            return pack_format

    @staticmethod
    def validate_map_layout(directory: Path) -> None:
        found_level = any(
            path.name == "level.dat" and path.is_file()
            for path in directory.rglob("level.dat")
        )
        if not found_level:
            raise ValidationError("invalid_map", "地图压缩包中未找到 level.dat")
