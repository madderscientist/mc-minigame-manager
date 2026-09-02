import gzip
import io
import logging
import re
import zipfile
from pathlib import Path, PurePosixPath

import nbtlib  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)
MAX_LEVEL_DAT_BYTES = 16 * 1024**2
MINECRAFT_VERSION = re.compile(
    r"^\s*(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?\s*$"
)


def required_java_major(mc_version: str) -> int:
    """Return the Java major required by Paper for a Minecraft release."""
    match = MINECRAFT_VERSION.fullmatch(mc_version)
    if match is None:
        raise ValueError("无法识别 Minecraft 版本")
    major, minor, patch = (int(value or 0) for value in match.groups())
    if major == 1:
        if (minor == 7 and patch >= 10) or 8 <= minor <= 11:
            return 8
        if 12 <= minor <= 15 or (minor == 16 and patch <= 4):
            return 11
        if minor == 16 and patch >= 5:
            return 16
        if 17 <= minor <= 19:
            return 17
        if minor == 20 or (minor == 21 and patch <= 11):
            return 21
    elif major == 26 and minor >= 1:
        return 25
    raise ValueError("该 Minecraft 版本不在当前 Paper Java 兼容表中")


def read_data_version(map_root: Path) -> int | None:
    """Read Minecraft DataVersion from level.dat without guessing an exact release."""
    candidates = sorted(
        map_root.rglob("level.dat"),
        key=lambda path: (len(path.parts), path.as_posix()),
    )
    for level_dat in candidates:
        try:
            document = nbtlib.load(level_dat)
            data = document.get("Data")
            if data is None or "DataVersion" not in data:
                continue
            return int(data["DataVersion"])
        except (OSError, ValueError, TypeError, KeyError, EOFError):
            logger.warning("Unable to parse DataVersion from %s", level_dat)
        except Exception:
            # nbtlib does not expose a stable public base exception across releases.
            logger.warning("Unable to parse DataVersion from %s", level_dat)
    return None


def read_minecraft_version(map_root: Path) -> str | None:
    candidates = sorted(
        map_root.rglob("level.dat"),
        key=lambda path: (len(path.parts), path.as_posix()),
    )
    for level_dat in candidates:
        try:
            version = _minecraft_version_from_document(nbtlib.load(level_dat))
            if version is not None:
                return version
        except Exception:
            logger.warning("Unable to read Minecraft version from %s", level_dat)
    return None


def read_minecraft_version_from_zip(archive: Path) -> str | None:
    try:
        with zipfile.ZipFile(archive) as bundle:
            selected = _selected_level_dat(bundle.infolist())
            if selected is None or selected.file_size > MAX_LEVEL_DAT_BYTES:
                return None
            try:
                with bundle.open(selected) as source:
                    raw = source.read(MAX_LEVEL_DAT_BYTES + 1)
                if len(raw) > MAX_LEVEL_DAT_BYTES:
                    return None
                document = _parse_nbt_payload(raw)
                return _minecraft_version_from_document(document)
            except Exception:
                return None
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def _selected_level_dat(
    entries: list[zipfile.ZipInfo],
) -> zipfile.ZipInfo | None:
    normalized: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info in entries:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or not path.parts or ".." in path.parts:
            continue
        normalized.append((info, path))
    top_level = {path.parts[0] for _, path in normalized}
    wrapped = len(top_level) == 1 and not any(
        len(path.parts) == 1 and not info.is_dir() for info, path in normalized
    )
    candidates: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info, path in normalized:
        if info.is_dir() or path.name != "level.dat":
            continue
        selected_path = PurePosixPath(*path.parts[1:]) if wrapped else path
        candidates.append((info, selected_path))
    root = next((info for info, path in candidates if path.parts == ("level.dat",)), None)
    if root is not None:
        return root
    shallow = sorted(
        (
            (info, path)
            for info, path in candidates
            if len(path.parent.parts) == 1
        ),
        key=lambda item: item[1].as_posix(),
    )
    return shallow[0][0] if shallow else None


def _parse_nbt_payload(raw: bytes) -> object:
    if raw.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as source:
            decoded = source.read(MAX_LEVEL_DAT_BYTES + 1)
        if len(decoded) > MAX_LEVEL_DAT_BYTES:
            raise ValueError("level.dat exceeds decompression limit")
        raw = decoded
    return nbtlib.File.parse(io.BytesIO(raw))


def _minecraft_version_from_document(document: object) -> str | None:
    if not isinstance(document, dict):
        return None
    data = document.get("Data")
    if not isinstance(data, dict):
        return None
    version = data.get("Version")
    if not isinstance(version, dict):
        return None
    name = str(version.get("Name", "")).strip()
    return name if name and MINECRAFT_VERSION.fullmatch(name) else None
