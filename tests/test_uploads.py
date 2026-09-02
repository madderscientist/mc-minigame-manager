import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mc_manager.errors import ConflictError
from mc_manager.services.uploads import ChunkedUploadStore


def make_store(tmp_path: Path) -> ChunkedUploadStore:
    return ChunkedUploadStore(
        tmp_path,
        max_bytes=1024,
        max_resource_pack_bytes=256,
        max_sessions=4,
        max_reserved_bytes=2048,
        chunk_size=4,
    )


def test_chunk_upload_is_idempotent_and_result_can_be_retried(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    upload_id = "d791ecac-ba64-4dbf-9fe3-5bfa4bbc2011"
    metadata = {"map_size": 6, "name": "Test"}
    assert store.create(upload_id, metadata) == (4, False)

    for index, data in enumerate((b"abcd", b"ef")):
        checksum = hashlib.sha256(data).hexdigest()
        store.write_chunk(upload_id, "map", index, data, checksum)
        store.write_chunk(upload_id, "map", index, data, checksum)

    with store.completion(upload_id) as completion:
        assert completion.result is None
        assert completion.upload is not None
        assert completion.upload.map_path.read_bytes() == b"abcdef"
        store.finish(upload_id, {"map_id": 7, "name": "Test", "mc_version": "1.20.4"})

    assert store.create(upload_id, metadata) == (4, True)
    with store.completion(upload_id) as completion:
        assert completion.upload is None
        assert completion.result == {
            "map_id": 7,
            "name": "Test",
            "mc_version": "1.20.4",
        }
    store.cancel(upload_id)
    assert (store.directory(upload_id) / "result.json").is_file()


def test_concurrent_different_writes_to_same_chunk_cannot_corrupt_marker(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    upload_id = "56ce9b84-744a-4a43-a69f-96cab44172db"
    store.create(upload_id, {"map_size": 4, "name": "Test"})
    barrier = threading.Barrier(2)

    def write(data: bytes) -> bytes:
        barrier.wait()
        store.write_chunk(upload_id, "map", 0, data, hashlib.sha256(data).hexdigest())
        return data

    successes: list[bytes] = []
    failures: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write, data) for data in (b"aaaa", b"bbbb")]
        for future in futures:
            try:
                successes.append(future.result())
            except BaseException as error:
                failures.append(error)

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ConflictError)
    directory = store.directory(upload_id)
    actual = (directory / "map.part").read_bytes()
    marker = (directory / "chunks" / "map-0").read_text(encoding="ascii")
    assert actual == successes[0]
    assert marker == hashlib.sha256(actual).hexdigest()


def test_concurrent_identical_writes_to_same_chunk_are_idempotent(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    upload_id = "8ae22716-b637-4649-aa09-fac3e3786600"
    store.create(upload_id, {"map_size": 4, "name": "Test"})
    data = b"same"
    checksum = hashlib.sha256(data).hexdigest()
    barrier = threading.Barrier(4)

    def write() -> None:
        barrier.wait()
        store.write_chunk(upload_id, "map", 0, data, checksum)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(write) for _ in range(4)]
        for future in futures:
            future.result()

    assert (store.directory(upload_id) / "map.part").read_bytes() == data


def test_completion_lock_rejects_late_chunk_write(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    upload_id = "ef5f2fef-f988-42c4-ae33-f710974e8fcf"
    store.create(upload_id, {"map_size": 4, "name": "Test"})
    data = b"data"
    checksum = hashlib.sha256(data).hexdigest()
    store.write_chunk(upload_id, "map", 0, data, checksum)

    with store.completion(upload_id), pytest.raises(ConflictError, match="正在完成"):
        store.write_chunk(upload_id, "map", 0, data, checksum)


def test_cleanup_removes_only_expired_uploads(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    old_id = "fb452783-86e3-40a2-ae12-8fdb8a30bcc8"
    fresh_id = "bed87e12-248d-4799-94b5-b488a835224d"
    metadata = {"map_size": 4, "name": "Test"}
    store.create(old_id, metadata)
    store.create(fresh_id, metadata)
    now = datetime.now(UTC)
    old_timestamp = (now - timedelta(days=2)).timestamp()
    os.utime(store.directory(old_id), (old_timestamp, old_timestamp))

    assert store.cleanup_expired(now) == 1
    assert not (store.root / old_id).exists()
    assert (store.root / fresh_id).exists()

    store.cancel(fresh_id)
    assert not (store.root / fresh_id).exists()


def test_global_session_capacity_is_enforced(tmp_path: Path) -> None:
    store = ChunkedUploadStore(
        tmp_path,
        max_bytes=16,
        max_resource_pack_bytes=4,
        max_sessions=1,
        max_reserved_bytes=16,
        chunk_size=4,
    )
    store.create("814d36c1-24d7-402b-babb-152ebe2c0a9d", {"map_size": 4, "name": "One"})

    with pytest.raises(ConflictError, match="会话已达上限"):
        store.create(
            "c631d074-2cbf-4050-9fc3-dd2187187f57",
            {"map_size": 4, "name": "Two"},
        )


def test_cancel_waits_for_create_and_does_not_leave_a_revived_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    upload_id = "38965426-b265-4e62-8623-5e6247694611"
    published = threading.Event()
    allow_publish = threading.Event()
    original_replace = os.replace

    def delayed_replace(source: Path, destination: Path) -> None:
        if destination == store.root / upload_id:
            published.set()
            assert allow_publish.wait(timeout=2)
        original_replace(source, destination)

    monkeypatch.setattr("mc_manager.services.uploads.os.replace", delayed_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        creating = executor.submit(
            store.create, upload_id, {"map_size": 4, "name": "Test"}
        )
        assert published.wait(timeout=2)
        canceling = executor.submit(store.cancel, upload_id)
        allow_publish.set()
        creating.result()
        canceling.result()

    assert not (store.root / upload_id).exists()
