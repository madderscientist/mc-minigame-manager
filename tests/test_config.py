import pytest
from pydantic import ValidationError

from mc_manager.config import Settings


def test_rejects_reversed_port_range() -> None:
    with pytest.raises(ValidationError, match="MC_PORT_MIN"):
        Settings(port_min=31001, port_max=31000)


def test_accepts_single_port_range() -> None:
    settings = Settings(port_min=31000, port_max=31000)
    assert settings.port_min == settings.port_max == 31000


def test_maps_local_game_ports_to_public_addresses() -> None:
    settings = Settings(
        port_min=30000,
        port_max=30002,
        public_game_host="jc1.top",
        public_game_port_min=6006,
    )
    assert settings.public_game_address(30000) == "jc1.top:6006"
    assert settings.public_game_address(30002) == "jc1.top:6008"
    assert settings.public_game_address(30003) is None


def test_requires_complete_public_game_mapping() -> None:
    with pytest.raises(ValidationError, match="configured together"):
        Settings(public_game_host="jc1.top")
    with pytest.raises(ValidationError, match="configured together"):
        Settings(public_game_port_min=6006)


def test_rejects_public_game_port_overflow() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        Settings(
            port_min=30000,
            port_max=30002,
            public_game_host="jc1.top",
            public_game_port_min=65535,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "http://packs.local",
        "http://127.0.0.1:8080",
        "http://10.0.0.1:8080",
        "http://169.254.1.1:8080",
        "http://[::1]:8080",
    ],
)
def test_rejects_non_public_resource_pack_url(url: str) -> None:
    with pytest.raises(ValidationError, match=r"local|private"):
        Settings(resource_pack_base_url=url)


def test_accepts_public_resource_pack_url() -> None:
    settings = Settings(resource_pack_base_url="https://packs.example.com/base/")
    assert settings.resource_pack_base_url == "https://packs.example.com/base"
