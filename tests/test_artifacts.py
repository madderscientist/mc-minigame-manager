from pathlib import Path
from typing import Any

import pytest

from mc_manager.errors import ValidationError
from mc_manager.services.artifacts import ArtifactManager


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.is_redirect = False

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


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
