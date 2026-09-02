from pathlib import Path

import nbtlib
import pytest

from mc_manager.services.versions import read_data_version, required_java_major


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
