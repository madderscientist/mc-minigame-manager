from pathlib import Path

import nbtlib

from mc_manager.services.versions import read_data_version


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
