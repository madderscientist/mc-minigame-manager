import json
import os
import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Final, cast

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


def update_server_operators(path: Path, operators: Mapping[str, str]) -> None:
    if not operators:
        return
    entries: list[dict[str, object]] = []
    if path.exists():
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Existing ops.json is unreadable or invalid") from error
        if not isinstance(payload, list):
            raise RuntimeError("Existing ops.json must contain a JSON array of objects")
        for raw_entry in cast(list[object], payload):
            if not isinstance(raw_entry, dict):
                raise RuntimeError("Existing ops.json must contain a JSON array of objects")
            entry: dict[str, object] = {}
            for key, value in cast(dict[object, object], raw_entry).items():
                if not isinstance(key, str):
                    raise RuntimeError("Existing ops.json contains a non-string key")
                entry[key] = value
            entries.append(entry)

    configured_uuids = set(operators)
    configured_names = {name.casefold() for name in operators.values()}
    retained_entries: list[dict[str, object]] = []
    for entry in entries:
        entry_name = entry.get("name")
        if entry.get("uuid") in configured_uuids or (
            isinstance(entry_name, str) and entry_name.casefold() in configured_names
        ):
            continue
        retained_entries.append(entry)
    entries = retained_entries
    entries.extend(
        {
            "uuid": player_uuid,
            "name": name,
            "level": 4,
            "bypassesPlayerLimit": False,
        }
        for player_uuid, name in operators.items()
    )
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)