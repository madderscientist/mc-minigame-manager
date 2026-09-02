import gzip
import io
import zipfile
from pathlib import Path

import nbtlib
import pytest

from mc_manager.services.versions import (
    MAX_LEVEL_DAT_BYTES,
    read_data_version,
    read_minecraft_version_from_zip,
    required_java_major,
)


def level_dat(version: str, *, compressed: bool) -> bytes:
    document = nbtlib.File(
        {
            "Data": nbtlib.Compound(
                {"Version": nbtlib.Compound({"Name": nbtlib.String(version)})}
            )
        }
    )
    output = io.BytesIO()
    document.write(output)
    raw = output.getvalue()
    return gzip.compress(raw) if compressed else raw


def map_archive(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return output.getvalue()


@pytest.mark.parametrize(
    ("mc_version", "java_major"),
    [
        ("1.7.10", 8),
        ("1.11.2", 8),
        ("1.12.2", 11),
        ("1.16.4", 11),
        ("1.16.5", 16),
        ("1.17.1", 17),
        ("1.19.4", 17),
        ("1.20.4", 21),
        ("1.21.11-rc3", 21),
        ("26.1", 25),
        ("26.2-rc-2", 25),
    ],
)
def test_selects_required_java_major(mc_version: str, java_major: int) -> None:
    assert required_java_major(mc_version) == java_major


@pytest.mark.parametrize("mc_version", ["", "1.7.9", "1.21.12", "25.1", "latest"])
def test_rejects_unsupported_java_version_mapping(mc_version: str) -> None:
    with pytest.raises(ValueError, match=r"无法识别|不在当前"):
        required_java_major(mc_version)


def test_reads_data_version_from_level_dat(tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    document = nbtlib.File(
        {"Data": nbtlib.Compound({"DataVersion": nbtlib.Int(3700)})}, gzipped=True
    )
    document.save(world / "level.dat")
    assert read_data_version(tmp_path) == 3700


def test_invalid_level_dat_returns_none(tmp_path: Path) -> None:
    (tmp_path / "level.dat").write_bytes(b"not-nbt")
    assert read_data_version(tmp_path) is None


@pytest.mark.parametrize("compressed", [True, False])
def test_reads_minecraft_version_from_zipped_level_dat(
    tmp_path: Path, compressed: bool
) -> None:
    archive = tmp_path / "map.zip"
    archive.write_bytes(
        map_archive({"wrapper/world/level.dat": level_dat("1.21.4", compressed=compressed)})
    )

    assert read_minecraft_version_from_zip(archive) == "1.21.4"


def test_version_reader_does_not_fallback_when_selected_root_is_invalid(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "map.zip"
    archive.write_bytes(
        map_archive(
            {
                "level.dat": b"invalid",
                "world/level.dat": level_dat("1.20.4", compressed=True),
            }
        )
    )

    assert read_minecraft_version_from_zip(archive) is None


def test_version_reader_selects_first_shallow_world_by_path(tmp_path: Path) -> None:
    archive = tmp_path / "map.zip"
    archive.write_bytes(
        map_archive(
            {
                "z_world/level.dat": level_dat("1.21.4", compressed=True),
                "a_world/level.dat": level_dat("1.20.4", compressed=True),
            }
        )
    )

    assert read_minecraft_version_from_zip(archive) == "1.20.4"


def test_version_reader_rejects_oversized_gzip_payload(tmp_path: Path) -> None:
    archive = tmp_path / "map.zip"
    archive.write_bytes(
        map_archive({"level.dat": gzip.compress(b"\0" * (MAX_LEVEL_DAT_BYTES + 1))})
    )

    assert read_minecraft_version_from_zip(archive) is None
