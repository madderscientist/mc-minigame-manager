import gzip
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import nbtlib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from starlette.datastructures import FormData, State

from mc_manager.app import _map_import_hash, create_app
from mc_manager.config import Settings
from mc_manager.enums import DesiredState, ObservedState, ResourceState
from mc_manager.models import MapRecord, RunRecord
from mc_manager.runtime.fake import FakeRuntime
from mc_manager.worker import Worker
from tests.conftest import make_map_zip, make_resource_pack_zip


def app_state(client: TestClient) -> State:
    return cast(FastAPI, client.app).state


def make_versioned_map_zip(version: str) -> bytes:
    document = nbtlib.File(
        {
            "Data": nbtlib.Compound(
                {
                    "Version": nbtlib.Compound({"Name": nbtlib.String(version)}),
                    "DataVersion": nbtlib.Int(3700),
                }
            )
        }
    )
    output = io.BytesIO()
    document.write(output)
    return make_map_zip({"level.dat": gzip.compress(output.getvalue())})


def run_task(client: TestClient, worker: Worker, task_id: str) -> dict:
    assert worker.run_once()
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


def create_ready_game(
    client: TestClient,
    worker: Worker,
    map_id: int,
    *,
    key: str | None = None,
) -> int:
    headers = {"Idempotency-Key": key} if key else {}
    response = client.post("/api/games", json={"map_id": map_id}, headers=headers)
    assert response.status_code == 202, response.text
    body = response.json()
    task = run_task(client, worker, body["task_id"])
    assert task["status"] == "succeeded"
    assert task["result"] == {"game_id": body["game_id"], "map_id": map_id}
    return int(body["game_id"])


def test_map_game_start_stop_backup_load_and_delete(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    settings: Settings,
    map_zip: bytes,
    upload_map,
) -> None:
    client, worker, _runtime = app_client
    map_id = upload_map(client, map_zip)

    maps = client.get("/api/maps")
    assert maps.status_code == 200
    assert maps.json()[0]["map_id"] == map_id
    assert "kind" not in maps.json()[0]

    create = client.post(
        "/api/games",
        json={"map_id": map_id, "name": "Round one"},
        headers={"Idempotency-Key": "create-game-1"},
    )
    assert create.status_code == 202, create.text
    duplicate_create = client.post(
        "/api/games",
        json={"map_id": map_id, "name": "Round one"},
        headers={"Idempotency-Key": "create-game-1"},
    )
    assert duplicate_create.json() == create.json()
    game_id = create.json()["game_id"]
    create_task = run_task(client, worker, create.json()["task_id"])
    assert create_task["status"] == "succeeded"

    game = client.get(f"/api/games/{game_id}").json()
    assert game["game_id"] == game_id
    assert game["map_id"] == map_id
    assert game["map_name"] == "Spleef"
    assert game["mc_version"] == "1.20.4"
    assert game["paper_build"] == "497"
    assert game["java_major"] == 21
    assert game["name"] == "Round one"
    assert game["state"] == "ready"

    start = client.post(
        "/api/start",
        json={"game_id": game_id},
        headers={"Idempotency-Key": "start-1"},
    )
    assert start.status_code == 202, start.text
    assert start.json()["port"] == 31000
    assert "run_id" not in start.json()
    reserved_port = next(
        port
        for port in client.get("/api/status").json()["ports"]
        if port["port"] == 31000
    )
    assert reserved_port["state"] == "reserved"
    assert reserved_port["game_id"] == game_id
    duplicate_start = client.post(
        "/api/start",
        json={"game_id": game_id},
        headers={"Idempotency-Key": "start-1"},
    )
    assert duplicate_start.json() == start.json()
    start_task = run_task(client, worker, start.json()["task_id"])
    assert start_task["status"] == "succeeded"
    assert start_task["result"] == {"game_id": game_id, "port": 31000}
    assert "run_id" not in start_task

    running_game = client.get(f"/api/games/{game_id}").json()
    assert running_game["public_address"] == "play.example.com:41000"
    running_status = client.get("/api/status").json()["running_games"][0]
    assert running_status["game_name"] == "Round one"
    assert running_status["mc_version"] == "1.20.4"
    assert running_status["public_address"] == "play.example.com:41000"

    already_running = client.post("/api/start", json={"game_id": game_id})
    assert already_running.status_code == 409

    game_path = settings.game_root / str(game_id)
    assert (game_path / "world" / "level.dat").is_file()
    assert "server-port=25565" in (game_path / "server.properties").read_text()
    (game_path / "progress.dat").write_bytes(b"checkpoint-one")

    map_in_use = client.delete(f"/api/maps/{map_id}")
    assert map_in_use.status_code == 409
    stop = client.post(
        "/api/stop",
        json={"game_id": game_id},
        headers={"Idempotency-Key": "stop-1"},
    )
    assert stop.status_code == 202, stop.text
    duplicate_stop = client.post("/api/stop", json={"game_id": game_id})
    assert duplicate_stop.status_code == 202
    assert duplicate_stop.json()["task_id"] == stop.json()["task_id"]
    stop_task = run_task(client, worker, stop.json()["task_id"])
    assert stop_task["status"] == "succeeded"

    backups = client.get(f"/api/games/{game_id}/backups").json()
    assert len(backups) == 1
    backup_id = backups[0]["backup_id"]
    assert backups[0]["clean_shutdown"] is True

    (game_path / "progress.dat").write_bytes(b"checkpoint-two")
    load = client.post(
        "/api/load",
        json={"game_id": game_id, "backup_id": backup_id},
        headers={"Idempotency-Key": "load-1"},
    )
    assert load.status_code == 202, load.text
    load_task = run_task(client, worker, load.json()["task_id"])
    assert load_task["status"] == "succeeded"
    assert load_task["result"]["game_id"] == game_id
    assert load_task["result"]["protection_backup_id"] != backup_id
    assert (game_path / "progress.dat").read_bytes() == b"checkpoint-one"

    assert client.delete(f"/api/games/{game_id}/backups/{backup_id}").status_code == 204
    remaining = client.get(f"/api/games/{game_id}/backups").json()
    assert all(backup["backup_id"] != backup_id for backup in remaining)

    delete_game = client.delete(
        f"/api/games/{game_id}", headers={"Idempotency-Key": "delete-game-1"}
    )
    assert delete_game.status_code == 202
    delete_task = run_task(client, worker, delete_game.json()["task_id"])
    assert delete_task["status"] == "succeeded"
    assert client.get(f"/api/games/{game_id}").status_code == 404
    assert client.delete(f"/api/maps/{map_id}").status_code == 204


def test_each_game_has_its_own_id_and_files(
    app_client: tuple[TestClient, Worker, FakeRuntime], map_zip: bytes, upload_map
) -> None:
    client, worker, _runtime = app_client
    map_id = upload_map(client, map_zip)
    first = create_ready_game(client, worker, map_id)
    second = create_ready_game(client, worker, map_id)
    assert first != second
    games = client.get("/api/games").json()
    assert {game["game_id"] for game in games} == {first, second}
    assert {game["map_id"] for game in games} == {map_id}


def test_paper_permissions_are_enforced_on_import_and_start(
    app_client: tuple[TestClient, Worker, FakeRuntime], upload_map
) -> None:
    client, worker, _runtime = app_client
    worker.settings.default_operators_json = (
        '{"TestOperator":"123e4567e89b42d3a456426614174000"}'
    )
    archive = make_map_zip(
        {
            "world/level.dat": b"test-level",
            "server.properties": (
                b"enable-command-block=false\n"
                b"function-permission-level=1\n"
                b"op-permission-level=1\n"
            ),
        }
    )
    map_id = upload_map(client, archive)
    map_properties = (
        app_state(client).settings.map_root / str(map_id) / "server.properties"
    ).read_text()
    assert "enable-command-block=true" in map_properties
    assert "function-permission-level=4" in map_properties
    assert "op-permission-level=4" in map_properties

    game_id = create_ready_game(client, worker, map_id)
    properties_path = (
        app_state(client).settings.game_root / str(game_id) / "server.properties"
    )
    properties_path.write_text(
        properties_path.read_text()
        .replace("enable-command-block=true", "enable-command-block=false")
        .replace("function-permission-level=4", "function-permission-level=1")
        .replace("op-permission-level=4", "op-permission-level=1")
    )

    start = client.post("/api/start", json={"game_id": game_id})
    assert start.status_code == 202, start.text
    task = run_task(client, worker, start.json()["task_id"])
    assert task["status"] == "succeeded"
    started_properties = properties_path.read_text()
    assert "enable-command-block=true" in started_properties
    assert "function-permission-level=4" in started_properties
    assert "op-permission-level=4" in started_properties
    operators = json.loads((properties_path.parent / "ops.json").read_text())
    assert operators == [
        {
            "uuid": "123e4567-e89b-42d3-a456-426614174000",
            "name": "TestOperator",
            "level": 4,
            "bypassesPlayerLimit": False,
        }
    ]


def test_game_list_avoids_per_game_run_queries(
    app_client: tuple[TestClient, Worker, FakeRuntime], map_zip: bytes, upload_map
) -> None:
    client, worker, _runtime = app_client
    map_id = upload_map(client, map_zip)
    for _ in range(5):
        create_ready_game(client, worker, map_id)

    statements: list[str] = []

    def capture_statement(
        _connection, _cursor, statement: str, _parameters, _context, _executemany
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = app_state(client).database.engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = client.get("/api/games")
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert len(response.json()) == 5
    assert len(statements) <= 2


def test_game_views_use_latest_run(
    app_client: tuple[TestClient, Worker, FakeRuntime], map_zip: bytes, upload_map
) -> None:
    client, worker, _runtime = app_client
    game_id = create_ready_game(client, worker, upload_map(client, map_zip))
    now = datetime.now(UTC)
    with app_state(client).database.session_factory.begin() as session:
        session.add_all(
            [
                RunRecord(
                    run_id="00000000-0000-4000-8000-000000000001",
                    game_id=game_id,
                    port=31000,
                    desired_state=DesiredState.STOPPED,
                    observed_state=ObservedState.STOPPED,
                    container_name="mc-test-old-run",
                    created_at=now - timedelta(minutes=1),
                ),
                RunRecord(
                    run_id="00000000-0000-4000-8000-000000000002",
                    game_id=game_id,
                    port=31001,
                    desired_state=DesiredState.RUNNING,
                    observed_state=ObservedState.READY,
                    container_name="mc-test-new-run",
                    created_at=now,
                ),
            ]
        )

    listed = next(
        game for game in client.get("/api/games").json() if game["game_id"] == game_id
    )
    detail = client.get(f"/api/games/{game_id}").json()
    assert listed["runtime_state"] == detail["runtime_state"] == "ready"
    assert listed["port"] == detail["port"] == 31001


def test_errors_are_not_tied_to_map_id(
    app_client: tuple[TestClient, Worker, FakeRuntime],
) -> None:
    client, _worker, _runtime = app_client
    response = client.post("/api/games", json={"map_id": 999999})
    assert response.status_code == 404
    assert "map_id" not in response.json()
    assert response.json()["error"]["code"] == "map_not_found"


def test_old_routes_are_removed(app_client: tuple[TestClient, Worker, FakeRuntime]) -> None:
    client, _worker, _runtime = app_client
    assert client.get("/api/list").status_code == 404
    assert client.post("/api/start?map_id=1").status_code == 422
    assert client.post("/api/map/upload").status_code == 404
    assert client.get("/api/operations/old").status_code == 404


def test_fastapi_serves_frontend_without_swallowing_api_routes(
    app_client: tuple[TestClient, Worker, FakeRuntime],
) -> None:
    client, _worker, _runtime = app_client
    root = client.get("/")
    assert root.status_code == 200
    assert "<!doctype html>" in root.text.lower()
    assert client.get("/games/42").status_code == 200
    assert client.get("/api/not-a-real-route").status_code == 404


def test_api_token_protects_api(settings: Settings) -> None:
    from mc_manager.app import create_app

    values = settings.model_dump()
    values["api_token"] = "secret-token"
    protected = Settings.model_validate(values)
    with TestClient(create_app(protected)) as client:
        assert client.get("/api/maps").status_code == 401
        allowed = client.get(
            "/api/maps", headers={"Authorization": "Bearer secret-token"}
        )
        assert allowed.status_code == 200
        assert client.get("/healthz").status_code == 200


def test_api_serializes_database_timestamps_as_utc(
    app_client: tuple[TestClient, Worker, FakeRuntime], map_zip: bytes, upload_map
) -> None:
    client, _worker, _runtime = app_client
    map_id = upload_map(client, map_zip)
    created_at = client.get(f"/api/maps/{map_id}").json()["created_at"]
    assert created_at.endswith("Z")


def test_map_upload_automatically_locks_latest_stable_paper_build(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    map_zip: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _worker, _runtime = app_client
    monkeypatch.setattr(
        "mc_manager.app.latest_stable_paper_build",
        lambda mc_version, *, user_agent: "497",
    )
    upload = client.post(
        "/api/maps",
        data={"name": "Auto build", "mc_version": "1.20.4"},
        files={"map": ("map.zip", map_zip, "application/zip")},
    )
    assert upload.status_code == 201, upload.text
    details = client.get(f"/api/maps/{upload.json()['map_id']}").json()
    assert details["paper_build"] == "497"
    assert details["java_major"] == 21


def test_map_upload_reads_minecraft_version_from_level_dat(
    app_client: tuple[TestClient, Worker, FakeRuntime],
) -> None:
    client, _worker, _runtime = app_client
    upload = client.post(
        "/api/maps",
        data={"name": "Detected", "paper_build": "497"},
        files={
            "map": (
                "map.zip",
                make_versioned_map_zip("1.20.4"),
                "application/zip",
            )
        },
    )

    assert upload.status_code == 201, upload.text
    assert upload.json()["mc_version"] == "1.20.4"


def test_map_upload_rejects_version_that_disagrees_with_level_dat(
    app_client: tuple[TestClient, Worker, FakeRuntime],
) -> None:
    client, _worker, _runtime = app_client
    upload = client.post(
        "/api/maps",
        data={"name": "Mismatch", "mc_version": "1.21.4", "paper_build": "497"},
        files={
            "map": (
                "map.zip",
                make_versioned_map_zip("1.20.4"),
                "application/zip",
            )
        },
    )

    assert upload.status_code == 422
    assert upload.json()["error"]["code"] == "mc_version_mismatch"


def test_map_upload_requires_manual_version_only_when_detection_fails(
    app_client: tuple[TestClient, Worker, FakeRuntime], map_zip: bytes
) -> None:
    client, _worker, _runtime = app_client
    upload = client.post(
        "/api/maps",
        data={"name": "Old map", "paper_build": "497"},
        files={"map": ("map.zip", map_zip, "application/zip")},
    )

    assert upload.status_code == 422
    assert upload.json()["error"]["code"] == "mc_version_required"


def test_chunked_upload_is_verified_completed_and_retried(
    app_client: tuple[TestClient, Worker, FakeRuntime],
) -> None:
    client, _worker, _runtime = app_client
    archive = make_versioned_map_zip("1.20.4")
    upload_id = "a850d3d9-c305-438a-b81d-43fcfafad85e"
    metadata = {
        "map_size": len(archive),
        "resource_pack_size": 0,
        "name": "Chunked map",
        "paper_build": "497",
    }
    created = client.post(f"/api/uploads/{upload_id}", json=metadata)
    assert created.status_code == 201, created.text
    assert created.json()["completed"] is False
    chunk_size = created.json()["chunk_size"]
    for index, offset in enumerate(range(0, len(archive), chunk_size)):
        chunk = archive[offset : offset + chunk_size]
        uploaded = client.put(
            f"/api/uploads/{upload_id}/map/{index}",
            content=chunk,
            headers={"X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()},
        )
        assert uploaded.status_code == 204, uploaded.text

    first = client.post(
        f"/api/uploads/{upload_id}/complete",
        headers={"Idempotency-Key": "chunked-map-1"},
    )
    second = client.post(
        f"/api/uploads/{upload_id}/complete",
        headers={"Idempotency-Key": "different-key-is-ignored-after-completion"},
    )
    recreated = client.post(f"/api/uploads/{upload_id}", json=metadata)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["mc_version"] == "1.20.4"
    assert recreated.json()["completed"] is True


def test_chunked_complete_is_idempotent_if_result_write_crashes(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _worker, _runtime = app_client
    archive = make_versioned_map_zip("1.20.4")
    upload_id = "ce273691-5695-4930-8711-d050e64f13d9"
    created = client.post(
        f"/api/uploads/{upload_id}",
        json={"map_size": len(archive), "name": "Crash safe", "paper_build": "497"},
    )
    chunk_size = created.json()["chunk_size"]
    for index, offset in enumerate(range(0, len(archive), chunk_size)):
        chunk = archive[offset : offset + chunk_size]
        response = client.put(
            f"/api/uploads/{upload_id}/map/{index}",
            content=chunk,
            headers={"X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()},
        )
        assert response.status_code == 204

    store = app_state(client).chunked_uploads
    original_finish = store.finish

    def crash_before_result(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated crash before result.json")

    monkeypatch.setattr(store, "finish", crash_before_result)
    with pytest.raises(OSError, match="simulated crash"):
        client.post(f"/api/uploads/{upload_id}/complete")
    monkeypatch.setattr(store, "finish", original_finish)

    retried = client.post(f"/api/uploads/{upload_id}/complete")
    assert retried.status_code == 200, retried.text
    assert len(client.get("/api/maps").json()) == 1


def test_chunked_upload_reports_missing_chunks(
    app_client: tuple[TestClient, Worker, FakeRuntime],
) -> None:
    client, _worker, _runtime = app_client
    upload_id = "a1fece6d-3409-43d9-83e9-e3a128e9ff62"
    created = client.post(
        f"/api/uploads/{upload_id}",
        json={"map_size": 4, "name": "Incomplete", "mc_version": "1.20.4"},
    )
    assert created.status_code == 201
    completed = client.post(f"/api/uploads/{upload_id}/complete")
    assert completed.status_code == 409
    assert completed.json()["error"]["code"] == "upload_incomplete"
    assert completed.json()["error"]["details"] == {"missing": [0]}


def test_map_upload_reuses_highest_existing_build_for_same_version(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    map_zip: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _worker, _runtime = app_client
    for build in ("496", "497"):
        response = client.post(
            "/api/maps",
            data={
                "name": f"Existing {build}",
                "mc_version": "1.20.4",
                "paper_build": build,
            },
            files={"map": ("map.zip", map_zip, "application/zip")},
        )
        assert response.status_code == 201, response.text

    def unexpected_papermc_query(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("同版本已有 build 时不应查询 PaperMC")

    monkeypatch.setattr(
        "mc_manager.app.latest_stable_paper_build", unexpected_papermc_query
    )
    upload = client.post(
        "/api/maps",
        data={"name": "Reuse build", "mc_version": "1.20.4"},
        files={"map": ("map.zip", map_zip, "application/zip")},
    )
    assert upload.status_code == 201, upload.text
    details = client.get(f"/api/maps/{upload.json()['map_id']}").json()
    assert details["paper_build"] == "497"


def test_resource_pack_is_configured_and_publicly_downloadable(
    settings: Settings, map_zip: bytes
) -> None:
    values = settings.model_dump()
    values.update(
        api_token="secret-token",
        resource_pack_base_url="https://play.example.com",
    )
    configured = Settings.model_validate(values)
    resource_pack = make_resource_pack_zip()
    with TestClient(create_app(configured)) as client:
        upload = client.post(
            "/api/maps",
            data={
                "name": "Pack map",
                "mc_version": "1.20.4",
                "paper_build": "497",
                "resource_pack_required": "true",
                "resource_pack_prompt": '需要此材质包, 输入 "go" 后正常游玩',
            },
            files={
                "map": ("map.zip", map_zip, "application/zip"),
                "resource_pack": ("visuals.zip", resource_pack, "application/zip"),
            },
            headers={"Authorization": "Bearer secret-token"},
        )
        assert upload.status_code == 201, upload.text
        map_id = upload.json()["map_id"]
        details = client.get(
            f"/api/maps/{map_id}",
            headers={"Authorization": "Bearer secret-token"},
        ).json()
        assert details["java_major"] == 21
        pack = details["resource_pack"]
        assert pack["filename"] == "visuals.zip"
        assert pack["pack_format"] == 22
        assert pack["required"] is True
        assert pack["sha1"] == hashlib.sha1(
            resource_pack, usedforsecurity=False
        ).hexdigest()
        assert pack["url"].startswith(
            f"https://play.example.com/resource-packs/maps/{map_id}/"
        )

        properties = (configured.map_root / str(map_id) / "server.properties").read_text()
        assert f"resource-pack={pack['url']}" in properties
        assert f"resource-pack-sha1={pack['sha1']}" in properties
        assert "require-resource-pack=true" in properties
        assert (
            'resource-pack-prompt={"text":"需要此材质包, 输入 \\\\"go\\\\" 后正常游玩"}'
            in properties
        )

        create = client.post(
            "/api/games",
            json={"map_id": map_id},
            headers={"Authorization": "Bearer secret-token"},
        )
        assert create.status_code == 202, create.text
        worker = Worker(
            configured,
            app_state(client).database,
            FakeRuntime(),
            worker_id="resource-pack-test-worker",
        )
        assert worker.run_once()
        game_id = create.json()["game_id"]
        game_properties = (
            configured.game_root / str(game_id) / "server.properties"
        ).read_text()
        assert f"resource-pack={pack['url']}" in game_properties

        download_path = pack["url"].removeprefix("https://play.example.com")
        download = client.get(download_path)
        assert download.status_code == 200
        assert download.content == resource_pack
        assert download.headers["content-type"] == "application/zip"
        assert download.headers["cache-control"].endswith("immutable")
        assert download.headers["etag"] == f'"sha1-{pack["sha1"]}"'
        assert download.headers["x-content-type-options"] == "nosniff"


def test_resource_pack_with_unsafe_filename_is_renamed(
    settings: Settings, map_zip: bytes
) -> None:
    values = settings.model_dump()
    values["resource_pack_base_url"] = "https://packs.example.com"
    configured = Settings.model_validate(values)
    with TestClient(create_app(configured)) as client:
        upload = client.post(
            "/api/maps",
            data={
                "name": "Renamed pack",
                "mc_version": "1.20.4",
                "paper_build": "497",
            },
            files={
                "map": ("map.zip", map_zip, "application/zip"),
                "resource_pack": (
                    "中文材质包.zip",
                    make_resource_pack_zip(),
                    "application/zip",
                ),
            },
        )
        assert upload.status_code == 201, upload.text
        details = client.get(f"/api/maps/{upload.json()['map_id']}").json()
        assert details["resource_pack"]["filename"] == "resources.zip"
        assert details["resource_pack"]["url"].endswith("/resources.zip")


def test_failed_resource_pack_upload_removes_temporary_files(
    settings: Settings, map_zip: bytes
) -> None:
    values = settings.model_dump()
    values["resource_pack_base_url"] = "https://packs.example.com"
    configured = Settings.model_validate(values)
    with TestClient(create_app(configured)) as client:
        response = client.post(
            "/api/maps",
            data={
                "name": "Invalid pack",
                "mc_version": "1.20.4",
                "paper_build": "497",
            },
            files={
                "map": ("map.zip", map_zip, "application/zip"),
                "resource_pack": ("中文材质包.zip", b"not a zip", "application/zip"),
            },
        )
    assert response.status_code == 422
    assert not any(configured.upload_root.iterdir())
    assert not any(configured.map_root.iterdir())


def test_uses_bundled_resources_zip_when_no_pack_is_uploaded(
    settings: Settings,
) -> None:
    values = settings.model_dump()
    values["resource_pack_base_url"] = "https://packs.example.com"
    configured = Settings.model_validate(values)
    resource_pack = make_resource_pack_zip()
    archive = make_map_zip(
        {"level.dat": b"test-level", "resources.zip": resource_pack}
    )
    with TestClient(create_app(configured)) as client:
        upload = client.post(
            "/api/maps",
            data={
                "name": "Bundled pack",
                "mc_version": "1.20.4",
                "paper_build": "497",
            },
            files={"map": ("map.zip", archive, "application/zip")},
        )
        assert upload.status_code == 201, upload.text
        map_id = upload.json()["map_id"]
        details = client.get(f"/api/maps/{map_id}").json()
        assert details["resource_pack"]["filename"] == "resources.zip"
        assert details["resource_pack"]["required"] is False
        assert not (configured.map_root / str(map_id) / "world" / "resources.zip").exists()


def test_uploaded_resource_pack_overrides_bundled_resources_zip(
    settings: Settings,
) -> None:
    values = settings.model_dump()
    values["resource_pack_base_url"] = "https://packs.example.com"
    configured = Settings.model_validate(values)
    archive = make_map_zip(
        {"level.dat": b"test-level", "resources.zip": b"invalid bundled pack"}
    )
    with TestClient(create_app(configured)) as client:
        upload = client.post(
            "/api/maps",
            data={
                "name": "Explicit pack",
                "mc_version": "1.20.4",
                "paper_build": "497",
            },
            files={
                "map": ("map.zip", archive, "application/zip"),
                "resource_pack": (
                    "visuals.zip",
                    make_resource_pack_zip(),
                    "application/zip",
                ),
            },
        )
        assert upload.status_code == 201, upload.text
        map_id = upload.json()["map_id"]
        details = client.get(f"/api/maps/{map_id}").json()
        assert details["resource_pack"]["filename"] == "visuals.zip"
        assert not (configured.map_root / str(map_id) / "world" / "resources.zip").exists()


def test_interrupted_import_recovers_bundled_resource_pack_metadata(
    settings: Settings,
) -> None:
    values = settings.model_dump()
    values["resource_pack_base_url"] = "https://packs.example.com"
    configured = Settings.model_validate(values)
    archive = make_map_zip(
        {"level.dat": b"test-level", "resources.zip": make_resource_pack_zip()}
    )
    headers = {"Idempotency-Key": "bundled-pack-crash"}
    fields = {
        "name": "Recovered bundled pack",
        "mc_version": "1.20.4",
        "paper_build": "497",
    }
    with TestClient(create_app(configured)) as client:
        first = client.post(
            "/api/maps",
            data=fields,
            files={"map": ("map.zip", archive, "application/zip")},
            headers=headers,
        )
        assert first.status_code == 201, first.text
        map_id = first.json()["map_id"]
        expected = client.get(f"/api/maps/{map_id}").json()["resource_pack"]
        with app_state(client).database.session_factory.begin() as session:
            record = session.get(MapRecord, map_id)
            assert record is not None
            record.state = ResourceState.PREPARING
            record.extra_metadata = {}

    changed_values = configured.model_dump()
    changed_values["resource_pack_base_url"] = "https://new-packs.example.com"
    changed = Settings.model_validate(changed_values)
    with TestClient(create_app(changed)) as client:
        retried = client.post(
            "/api/maps",
            data=fields,
            files={"map": ("map.zip", archive, "application/zip")},
            headers=headers,
        )
        assert retried.status_code == 201, retried.text
        recovered = client.get(f"/api/maps/{map_id}").json()["resource_pack"]
        assert recovered == expected
        assert recovered["url"].startswith("https://packs.example.com/")


def test_interrupted_import_rejects_resource_pack_url_digest_mismatch(
    settings: Settings,
) -> None:
    values = settings.model_dump()
    values["resource_pack_base_url"] = "https://packs.example.com"
    configured = Settings.model_validate(values)
    archive = make_map_zip(
        {"level.dat": b"test-level", "resources.zip": make_resource_pack_zip()}
    )
    fields = {
        "name": "Tampered pack URL",
        "mc_version": "1.20.4",
        "paper_build": "497",
    }
    headers = {"Idempotency-Key": "tampered-pack-url"}
    with TestClient(create_app(configured)) as client:
        first = client.post(
            "/api/maps",
            data=fields,
            files={"map": ("map.zip", archive, "application/zip")},
            headers=headers,
        )
        map_id = first.json()["map_id"]
        map_directory = configured.map_root / str(map_id)
        metadata_path = map_directory / ".mc-manager-resources" / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        original_url = metadata["url"]
        metadata["url"] = original_url.replace(metadata["sha1"], "0" * 40)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        properties_path = map_directory / "server.properties"
        properties_path.write_text(
            properties_path.read_text(encoding="utf-8").replace(
                original_url, metadata["url"]
            ),
            encoding="utf-8",
        )
        with app_state(client).database.session_factory.begin() as session:
            record = session.get(MapRecord, map_id)
            assert record is not None
            record.state = ResourceState.PREPARING
            record.extra_metadata = {}

        retried = client.post(
            "/api/maps",
            data=fields,
            files={"map": ("map.zip", archive, "application/zip")},
            headers=headers,
        )
        assert retried.status_code == 422
        assert retried.json()["error"]["code"] == "resource_pack_metadata_mismatch"


def test_map_publish_uses_target_filesystem_for_atomic_replace(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    map_zip: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _worker, _runtime = app_client
    original_replace = __import__("os").replace

    def same_parent_replace(source: str, destination: str) -> None:
        assert Path(source).parent == Path(destination).parent
        original_replace(source, destination)

    def reject_metadata_copy(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("directory metadata cannot be copied")

    monkeypatch.setattr("mc_manager.services.storage.os.replace", same_parent_replace)
    monkeypatch.setattr("mc_manager.services.storage.shutil.copystat", reject_metadata_copy)
    upload = client.post(
        "/api/maps",
        data={"name": "Cross mount", "mc_version": "1.20.4", "paper_build": "497"},
        files={"map": ("map.zip", map_zip, "application/zip")},
    )
    assert upload.status_code == 201, upload.text


def test_resource_pack_requires_public_base_url(
    app_client: tuple[TestClient, Worker, FakeRuntime], map_zip: bytes
) -> None:
    client, _worker, _runtime = app_client
    response = client.post(
        "/api/maps",
        data={
            "name": "Pack map",
            "mc_version": "1.20.4",
            "paper_build": "497",
        },
        files={
            "map": ("map.zip", map_zip, "application/zip"),
            "resource_pack": (
                "visuals.zip",
                make_resource_pack_zip(),
                "application/zip",
            ),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "resource_pack_base_url_missing"


def test_rejects_additional_upload_files(settings: Settings, map_zip: bytes) -> None:
    values = settings.model_dump()
    values["resource_pack_base_url"] = "https://packs.example.com"
    configured = Settings.model_validate(values)
    files = [
        ("map", ("map.zip", map_zip, "application/zip")),
        ("res1", ("attachment.zip", b"ordinary attachment", "application/zip")),
    ]

    with TestClient(create_app(configured)) as client:
        response = client.post(
            "/api/maps",
            data={
                "name": "Reserved resource name",
                "mc_version": "1.20.4",
                "paper_build": "497",
            },
            files=files,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unexpected_upload"


def test_map_import_removes_untrusted_resource_pack_url(
    app_client: tuple[TestClient, Worker, FakeRuntime], upload_map
) -> None:
    client, _worker, _runtime = app_client
    archive = make_map_zip(
        {
            "world/level.dat": b"test-level",
            "server.properties": (
                b"motd=Trusted map\n"
                b"resource-pack=https://attacker.invalid/pack.zip\n"
                b"resource-pack-sha1=0000000000000000000000000000000000000000\n"
                b"require-resource-pack=true\n"
            ),
        }
    )
    map_id = upload_map(client, archive)
    properties = (
        app_state(client).settings.map_root / str(map_id) / "server.properties"
    ).read_text()
    assert "motd=Trusted map" in properties
    assert "resource-pack=" not in properties
    assert "resource-pack-sha1=" not in properties
    assert "require-resource-pack=" not in properties


def test_map_upload_is_idempotent(
    app_client: tuple[TestClient, Worker, FakeRuntime], map_zip: bytes
) -> None:
    client, _worker, _runtime = app_client
    data = {
        "name": "Idempotent map",
        "mc_version": "1.20.4",
        "paper_build": "497",
        "java_major": "17",
    }
    headers = {"Idempotency-Key": "map-upload-1"}
    first = client.post(
        "/api/maps",
        data=data,
        files={"map": ("map.zip", map_zip, "application/zip")},
        headers=headers,
    )
    second = client.post(
        "/api/maps",
        data=data,
        files={"map": ("map.zip", map_zip, "application/zip")},
        headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["map_id"] == first.json()["map_id"]

    changed = client.post(
        "/api/maps",
        data={**data, "name": "Different map"},
        files={"map": ("map.zip", map_zip, "application/zip")},
        headers=headers,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_key_reused"


@pytest.mark.parametrize("interrupted_state", [ResourceState.PREPARING, ResourceState.FAILED])
def test_map_upload_resumes_interrupted_record_after_crash(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    map_zip: bytes,
    interrupted_state: ResourceState,
) -> None:
    client, _worker, _runtime = app_client
    fields = {
        "name": "Recovered map",
        "mc_version": "1.20.4",
        "paper_build": "497",
        "java_major": "17",
    }
    import_hash = _map_import_hash(
        map_digest=hashlib.sha256(map_zip).hexdigest(),
        fields=fields,
        java_major=21,
    )
    with app_state(client).database.session_factory() as session:
        record = MapRecord(
            state=interrupted_state,
            name="Recovered map",
            mc_version="1.20.4",
            paper_build="497",
            java_major=21,
            relative_path="pending",
            import_idempotency_key="map-upload-crashed",
            import_request_hash=import_hash,
            extra_metadata={},
        )
        session.add(record)
        session.flush()
        record.relative_path = f"maps/{record.map_id}"
        expected_map_id = record.map_id
        session.commit()

    response = client.post(
        "/api/maps",
        data=fields,
        files={"map": ("map.zip", map_zip, "application/zip")},
        headers={"Idempotency-Key": "map-upload-crashed"},
    )
    assert response.status_code == 201
    assert response.json()["map_id"] == expected_map_id
    assert (app_state(client).settings.map_root / str(expected_map_id)).is_dir()


def test_map_upload_rejects_oversized_request_before_form_parsing(
    app_client: tuple[TestClient, Worker, FakeRuntime], settings: Settings
) -> None:
    client, _worker, _runtime = app_client
    limit = settings.max_upload_bytes + settings.max_upload_overhead_bytes
    response = client.post(
        "/api/maps",
        content=b"not-a-multipart-body",
        headers={
            "Content-Type": "multipart/form-data; boundary=test",
            "Content-Length": str(limit + 1),
        },
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"


def test_map_upload_closes_form_files_on_early_validation_error(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    map_zip: bytes,
    monkeypatch,
) -> None:
    client, _worker, _runtime = app_client
    closed = False
    original_close = FormData.close

    async def track_close(form: FormData) -> None:
        nonlocal closed
        closed = True
        await original_close(form)

    monkeypatch.setattr(FormData, "close", track_close)
    response = client.post(
        "/api/maps",
        data={
            "name": "Duplicate map",
            "mc_version": "1.20.4",
            "paper_build": "497",
        },
        files=[
            ("map", ("map.zip", map_zip, "application/zip")),
            ("map", ("other.zip", map_zip, "application/zip")),
        ],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "duplicate_map"
    assert closed


def test_stop_without_backup_releases_game_and_port(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    map_zip: bytes,
    upload_map,
) -> None:
    client, worker, _runtime = app_client
    map_id = upload_map(client, map_zip)
    game_id = create_ready_game(client, worker, map_id)
    start = client.post("/api/start", json={"game_id": game_id}).json()
    run_task(client, worker, start["task_id"])

    stop = client.post("/api/stop", json={"game_id": game_id, "backup": False})
    assert stop.status_code == 202
    stopped = run_task(client, worker, stop.json()["task_id"])
    assert stopped["status"] == "succeeded"
    assert stopped["result"] == {
        "game_id": game_id,
        "backup_id": None,
        "clean_shutdown": True,
    }
    assert client.get(f"/api/games/{game_id}/backups").json() == []

    status_body = client.get("/api/status").json()
    assert status_body["running_games"] == []
    port = next(item for item in status_body["ports"] if item["port"] == start["port"])
    assert port["state"] == "free"
    assert port["game_id"] is None


def test_backup_failure_releases_stopped_game(
    app_client: tuple[TestClient, Worker, FakeRuntime],
    map_zip: bytes,
    upload_map,
    monkeypatch,
) -> None:
    client, worker, _runtime = app_client
    map_id = upload_map(client, map_zip)
    game_id = create_ready_game(client, worker, map_id)
    start = client.post("/api/start", json={"game_id": game_id}).json()
    run_task(client, worker, start["task_id"])

    def fail_backup(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(worker.backups, "create", fail_backup)
    stop = client.post("/api/stop", json={"game_id": game_id})
    assert stop.status_code == 202
    failed = run_task(client, worker, stop.json()["task_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "storage_error"
    assert "disk full" not in failed["error_message"]
    status_body = client.get("/api/status").json()
    assert status_body["running_games"] == []
    port = next(item for item in status_body["ports"] if item["port"] == start["port"])
    assert port["state"] == "free"
    assert port["game_id"] is None
