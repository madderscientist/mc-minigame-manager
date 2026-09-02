import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Final

PAPER_PERMISSION_PROPERTIES: Final[dict[str, str]] = {
    "enable-command-block": "true",
    "function-permission-level": "4",
    "op-permission-level": "4",
}
SAFE_PROPERTY_KEY: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe_property_value(value: str) -> str:
    trailing_backslashes = len(value) - len(value.rstrip("\\"))
    return value + "\\" if trailing_backslashes % 2 else value


def update_server_properties(
    path: Path,
    updates: Mapping[str, str],
    *,
    remove: Collection[str] = (),
) -> None:
    properties: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                if SAFE_PROPERTY_KEY.fullmatch(key):
                    properties[key] = value.strip()
    for key in remove:
        properties.pop(key, None)
    properties.update(updates)
    path.write_text(
        "# Managed by mc-minigame-manager\n"
        + "".join(
            f"{key}={_safe_property_value(value)}\n"
            for key, value in sorted(properties.items())
        ),
        encoding="utf-8",
    )