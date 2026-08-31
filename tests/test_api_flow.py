from fastapi.testclient import TestClient

from mc_manager.config import Settings
from mc_manager.runtime.fake import FakeRuntime
from mc_manager.worker import Worker


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
