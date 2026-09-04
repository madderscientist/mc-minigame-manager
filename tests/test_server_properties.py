from pathlib import Path

import pytest
from pydantic import ValidationError

from mc_manager.schemas import ServerSettings
from mc_manager.services.server_properties import update_server_properties
from mc_manager.services.server_settings import apply_server_settings


def test_writer_prevents_java_line_continuation_into_managed_values(
    tmp_path: Path,
) -> None:
    properties = tmp_path / "server.properties"
    properties.write_text(
        "aaa=untrusted\\\n"
        "unsafe key=value\n"
        "enable-command-block=false\n",
        encoding="utf-8",
    )

    update_server_properties(
        properties,
        {
            "enable-command-block": "true",
            "level-name": "world",
        },
    )

    lines = properties.read_text(encoding="utf-8").splitlines()
    untrusted = next(line for line in lines if line.startswith("aaa="))
    assert len(untrusted) - len(untrusted.rstrip("\\")) == 2
    assert "unsafe key=" not in lines
    assert "enable-command-block=true" in lines
    assert "level-name=world" in lines


def test_server_settings_write_structured_and_custom_properties(tmp_path: Path) -> None:
    properties = tmp_path / "server.properties"

    apply_server_settings(
        properties,
        ServerSettings(
            spawn_protection=0,
            gamemode="adventure",
            pvp=False,
            view_distance=12,
            custom={"network-compression-threshold": "512"},
        ),
    )

    assert properties.read_text(encoding="utf-8").splitlines() == [
        "# Managed by mc-minigame-manager",
        "gamemode=adventure",
        "network-compression-threshold=512",
        "pvp=false",
        "spawn-protection=0",
        "view-distance=12",
    ]


def test_server_settings_remove_inherited_property_and_force_system_value(
    tmp_path: Path,
) -> None:
    properties = tmp_path / "server.properties"
    properties.write_text("pvp=false\nserver-port=30000\n", encoding="utf-8")

    apply_server_settings(
        properties,
        {"spawn_protection": 8},
        inherited={"pvp": False, "spawn_protection": 16},
        forced={"server-port": "25565"},
    )

    lines = properties.read_text(encoding="utf-8").splitlines()
    assert "pvp=false" not in lines
    assert "spawn-protection=8" in lines
    assert "server-port=25565" in lines


@pytest.mark.parametrize(
    "custom",
    [
        {"server-port": "30000"},
        {"resource-pack": "https://example.invalid/pack.zip"},
        {"spawn-protection": "0"},
        {"motd": "hello\nserver-port=30000"},
        {"unsafe key": "value"},
    ],
)
def test_server_settings_reject_unsafe_custom_properties(
    custom: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        ServerSettings(custom=custom)
