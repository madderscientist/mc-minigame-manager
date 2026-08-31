import logging
from pathlib import Path

import nbtlib  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


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
