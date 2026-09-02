import logging
import re
from pathlib import Path

import nbtlib  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)
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
    candidates = sorted(map_root.rglob("level.dat"), key=lambda path: len(path.parts))
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
