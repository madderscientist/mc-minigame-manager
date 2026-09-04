from collections.abc import Mapping
from pathlib import Path
from typing import Final

from mc_manager.schemas import ServerSettings
from mc_manager.services.server_properties import update_server_properties

PROPERTY_NAMES: Final[dict[str, str]] = {
    "allow_flight": "allow-flight",
    "difficulty": "difficulty",
    "gamemode": "gamemode",
    "generate_structures": "generate-structures",
    "hardcore": "hardcore",
    "level_seed": "level-seed",
    "level_type": "level-type",
    "max_players": "max-players",
    "pvp": "pvp",
    "simulation_distance": "simulation-distance",
    "spawn_protection": "spawn-protection",
    "view_distance": "view-distance",
    "white_list": "white-list",
}


def server_settings_properties(
    settings: ServerSettings | Mapping[str, object],
) -> dict[str, str]:
    model = (
        settings
        if isinstance(settings, ServerSettings)
        else ServerSettings.model_validate(settings)
    )
    payload = model.model_dump(exclude_none=True)
    custom = payload.pop("custom", {})
    properties = {
        PROPERTY_NAMES[key]: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in payload.items()
    }
    properties.update(custom)
    return properties


def apply_server_settings(
    path: Path,
    settings: ServerSettings | Mapping[str, object],
    *,
    inherited: ServerSettings | Mapping[str, object] | None = None,
    forced: Mapping[str, str] | None = None,
) -> None:
    properties = server_settings_properties(settings)
    inherited_properties = server_settings_properties(inherited or {})
    update_server_properties(
        path,
        {**properties, **(forced or {})},
        remove=inherited_properties.keys() - properties.keys(),
    )