from pathlib import Path
from typing import Any

import pytest

from mc_manager.errors import ValidationError
from mc_manager.services.artifacts import (
    ArtifactManager,
    latest_stable_paper_build,
    supported_paper_versions,
)


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.is_redirect = False

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


def test_selects_latest_stable_paper_build(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {"id": 496, "channel": "STABLE", "downloads": {"server:default": {}}},
        {"id": 499, "channel": "EXPERIMENTAL", "downloads": {"server:default": {}}},
        {"id": 497, "channel": "STABLE", "downloads": {"server:default": {}}},
    ]
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse(payload))
    assert (
        latest_stable_paper_build("1.20.4", user_agent="test/1.0 (test@example.com)")
        == "497"
    )


def test_lists_supported_release_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "versions": {
            "26.1": ["26.1.2", "26.1-rc1"],
            "1.21": ["1.21.11", "1.21.11-pre1"],
            "1.6": ["1.6.4"],
        }
    }
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse(payload))

    assert supported_paper_versions("test/1.0 (test@example.com)") == [
        ("26.1.2", 25),
        ("1.21.11", 21),
    ]


def test_rejects_invalid_paper_version_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse({"versions": []}))

    with pytest.raises(ValidationError, match="返回格式无效"):
        supported_paper_versions("test/1.0 (test@example.com)")


def test_resolves_exact_stable_paper_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = [
        {
            "id": 497,
            "channel": "STABLE",
            "downloads": {
                "server:default": {
                    "url": "https://fill-data.papermc.io/paper.jar",
                    "checksums": {"sha256": "a" * 64},
                }
            },
        }
    ]
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse(payload))
    manager = ArtifactManager(tmp_path, user_agent="test/1.0 (test@example.com)")
    artifact = manager.resolve_paper("1.20.4", "497")
    assert artifact.sha256 == "a" * 64
    assert artifact.url.startswith("https://fill-data.papermc.io/")


def test_rejects_unstable_paper_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = [{"id": 1, "channel": "ALPHA", "downloads": {}}]
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: FakeResponse(payload))
    manager = ArtifactManager(tmp_path, user_agent="test/1.0 (test@example.com)")
    with pytest.raises(ValidationError, match="稳定版本"):
        manager.resolve_paper("1.21", "1")
