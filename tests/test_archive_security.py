import io
import stat
import zipfile
from pathlib import Path

import pytest

from mc_manager.config import Settings
from mc_manager.errors import ValidationError
from mc_manager.services.archive import SafeZipExtractor
from tests.conftest import make_map_zip, make_resource_pack_zip


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/windows.txt"])
def test_rejects_unsafe_paths(tmp_path: Path, settings: Settings, name: str) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_bytes(make_map_zip({name: b"bad", "level.dat": b"level"}))
    with pytest.raises(ValidationError, match=r"非法路径|绝对路径"):
        SafeZipExtractor(settings).extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_requires_level_dat(tmp_path: Path) -> None:
    directory = tmp_path / "map"
    directory.mkdir()
    (directory / "readme.txt").write_text("not a world")
    with pytest.raises(ValidationError, match=r"level\.dat"):
        SafeZipExtractor.validate_map_layout(directory)


def test_rejects_symlink_entry(tmp_path: Path, settings: Settings) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("world/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../outside")
    source = tmp_path / "symlink.zip"
    source.write_bytes(buffer.getvalue())
    with pytest.raises(ValidationError, match="符号链接"):
        SafeZipExtractor(settings).extract(source, tmp_path / "out")


def test_validates_resource_pack_metadata(tmp_path: Path, settings: Settings) -> None:
    source = tmp_path / "pack.zip"
    source.write_bytes(make_resource_pack_zip(pack_format=22))
    assert SafeZipExtractor(settings).validate_resource_pack(source) == 22


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"nested/pack.mcmeta": b'{}'}, "根目录"),
        ({"pack.mcmeta": b'{}'}, "元数据"),
        ({"pack.mcmeta": b'{"pack":{"pack_format":true}}'}, "必须是整数"),
    ],
)
def test_rejects_invalid_resource_pack_metadata(
    tmp_path: Path,
    settings: Settings,
    files: dict[str, bytes],
    message: str,
) -> None:
    source = tmp_path / "bad-pack.zip"
    source.write_bytes(make_map_zip(files))
    with pytest.raises(ValidationError, match=message):
        SafeZipExtractor(settings).validate_resource_pack(source)
