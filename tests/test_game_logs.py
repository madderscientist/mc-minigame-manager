import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from mc_manager.errors import ManagerError
from mc_manager.services.game_logs import MAX_LOG_BYTES, read_game_logs


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    path = tmp_path / "games" / "1" / "logs" / "latest.log"
    path.parent.mkdir(parents=True)
    return path


@pytest.mark.parametrize(
    ("content", "tail", "expected", "truncated"),
    [
        (b"", 500, [], False),
        (b"first\nsecond\nlast\n", 2, ["second", "last"], True),
        (b"first\r\nlast", 2, ["first", "last"], False),
        ("玩家加入游戏\n".encode(), 500, ["玩家加入游戏"], False),
        (b"invalid \xff\n", 500, ["invalid \ufffd"], False),
    ],
)
def test_log_tail(tmp_path, log_path, content, tail, expected, truncated):
    log_path.write_bytes(content)
    result = read_game_logs(tmp_path, "games/1", tail)
    assert result.available
    assert result.lines == expected
    assert result.truncated is truncated


def test_missing_logs(tmp_path):
    result = read_game_logs(tmp_path, "games/1", 500)
    assert not result.available
    assert not result.truncated
    assert result.lines == []


def test_bounds_bytes_and_discards_partial_line(tmp_path, log_path, monkeypatch):
    log_path.write_bytes(b"x" * (MAX_LOG_BYTES * 2) + b"\nlatest\n")
    original_read = os.read
    read_sizes = []

    def bounded_read(descriptor, size):
        read_sizes.append(size)
        return original_read(descriptor, size)

    monkeypatch.setattr("mc_manager.services.game_logs.os.read", bounded_read)
    result = read_game_logs(tmp_path, "games/1", 500)
    assert result.lines == ["latest"]
    assert result.truncated
    assert read_sizes == [MAX_LOG_BYTES + 1]


def test_byte_boundary_preserves_complete_first_line(tmp_path, log_path):
    log_path.write_bytes(b"old\n" + b"x" * (MAX_LOG_BYTES - 8) + b"\nlatest\n")
    result = read_game_logs(tmp_path, "games/1", 500)
    assert result.lines == ["x" * (MAX_LOG_BYTES - 8), "latest"]
    assert result.truncated


def test_oversized_single_line_is_bounded(tmp_path, log_path):
    log_path.write_bytes(b"x" * (MAX_LOG_BYTES * 2))
    result = read_game_logs(tmp_path, "games/1", 500)
    assert result.lines == ["x" * MAX_LOG_BYTES]
    assert result.truncated


@pytest.mark.parametrize("target", ["games", "games/1", "games/1/logs", "games/1/logs/latest.log"])
def test_refuses_symlinks_at_each_level(tmp_path, log_path, target):
    log_path.write_text("private content", encoding="utf-8")
    source = tmp_path / target
    moved = tmp_path / "hidden"
    source.rename(moved)
    source.symlink_to(moved)
    with pytest.raises(ManagerError, match="无法读取游戏日志"):
        read_game_logs(tmp_path, "games/1", 500)


@pytest.mark.parametrize("relative_path", ["../outside", "/etc", "."])
def test_refuses_unsafe_paths(tmp_path, relative_path):
    with pytest.raises(ManagerError, match="游戏日志路径无效"):
        read_game_logs(tmp_path, relative_path, 500)


def test_fifo_does_not_block(tmp_path, log_path):
    os.mkfifo(log_path)
    with pytest.raises(ManagerError, match="不是普通文件"):
        read_game_logs(tmp_path, "games/1", 500)


def test_permission_error_is_reported(tmp_path, log_path, monkeypatch):
    def deny(*args, **kwargs):
        raise PermissionError("private filesystem path")

    monkeypatch.setattr("mc_manager.services.game_logs.os.open", deny)
    with pytest.raises(ManagerError) as error:
        read_game_logs(tmp_path, "games/1", 500)
    assert error.value.code == "game_logs_unreadable"
    assert "private filesystem path" not in str(error.value)


@pytest.fixture
def ready_game(app_client, upload_map, map_zip):
    client, worker, _ = app_client
    map_id = upload_map(client, map_zip)
    created = client.post("/api/games", json={"map_id": map_id})
    assert created.status_code == 202
    assert worker.run_once()
    game_id = created.json()["game_id"]
    assert client.get(f"/api/games/{game_id}").json()["state"] == "ready"
    return game_id


def test_logs_api_missing_then_present(app_client, settings, ready_game):
    client, _, _ = app_client
    url = f"/api/games/{ready_game}/logs"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "game_id": ready_game, "lines": [], "available": False, "truncated": False
    }

    path = settings.game_root / str(ready_game) / "logs" / "latest.log"
    path.parent.mkdir()
    path.write_text("old\n玩家加入游戏\nlast\n", encoding="utf-8")
    response = client.get(url, params={"tail": 2})
    assert response.status_code == 200
    assert response.json() == {
        "game_id": ready_game,
        "lines": ["玩家加入游戏", "last"],
        "available": True,
        "truncated": True,
    }
    # Reopening the path on each request observes log rotation and truncation.
    path.rename(path.with_name("previous.log"))
    path.write_text("new run\n", encoding="utf-8")
    assert client.get(url).json()["lines"] == ["new run"]


def test_logs_api_missing_game(app_client):
    client, _, _ = app_client
    response = client.get("/api/games/999/logs")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "game_not_found"


@pytest.mark.parametrize("tail", [0, -1, 1001, "invalid"])
def test_logs_api_tail_validation(app_client, tail):
    client, _, _ = app_client
    assert client.get("/api/games/999/logs", params={"tail": tail}).status_code == 422


def test_logs_api_requires_existing_auth(app_client, settings):
    client, _, _ = app_client
    settings.api_token = SecretStr("test-token")
    assert client.get("/api/games/999/logs").status_code == 401
    response = client.get("/api/games/999/logs", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 404