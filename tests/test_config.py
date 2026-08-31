import pytest
from pydantic import ValidationError

from mc_manager.config import Settings


def test_rejects_reversed_port_range() -> None:
    with pytest.raises(ValidationError, match="MC_PORT_MIN"):
        Settings(port_min=31001, port_max=31000)


def test_accepts_single_port_range() -> None:
    settings = Settings(port_min=31000, port_max=31000)
    assert settings.port_min == settings.port_max == 31000


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
