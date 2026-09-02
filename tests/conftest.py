import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mc_manager.app import create_app
from mc_manager.config import Settings
from mc_manager.runtime.fake import FakeRuntime
from mc_manager.worker import Worker


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'manager.db'}",
        storage_root=tmp_path / "storage",
        port_min=31000,
        port_max=31002,
        public_game_host="play.example.com",
        public_game_port_min=41000,
        backup_limit=3,
        runtime_backend="fake",
        start_timeout_seconds=10,
        max_upload_bytes=8 * 1024 * 1024,
        max_extracted_bytes=16 * 1024 * 1024,
        max_archive_files=100,
        max_single_file_bytes=8 * 1024 * 1024,
    )


@pytest.fixture
def app_client(settings: Settings) -> Iterator[tuple[TestClient, Worker, FakeRuntime]]:
    app = create_app(settings)
    runtime = FakeRuntime()
    with TestClient(app) as client:
        worker = Worker(settings, app.state.database, runtime, worker_id="test-worker")
        yield client, worker, runtime


def make_map_zip(files: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in (files or {"level.dat": b"test-level"}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_resource_pack_zip(pack_format: int = 22) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "pack.mcmeta",
            json.dumps(
                {"pack": {"pack_format": pack_format, "description": "Test pack"}}
            ),
        )
        archive.writestr("assets/minecraft/textures/example.png", b"not-a-real-png")
    return buffer.getvalue()


@pytest.fixture
def map_zip() -> bytes:
    return make_map_zip()


@pytest.fixture
def upload_map():
    def upload(client: TestClient, archive: bytes) -> int:
        response = client.post(
            "/api/maps",
            data={
                "name": "Spleef",
                "mc_version": "1.20.4",
                "paper_build": "497",
                "java_major": "17",
            },
            files={"map": ("map.zip", archive, "application/zip")},
        )
        assert response.status_code == 201, response.text
        return int(response.json()["map_id"])

    return upload
