from pathlib import Path

from mc_manager.services.storage import Storage


def test_temporary_sibling_stays_on_source_mount(tmp_path: Path) -> None:
    source = tmp_path / "games" / "3"
    temporary = Storage.temporary_sibling(source, "delete-game-3")

    assert temporary.parent == source.parent
    assert temporary.name.startswith(".delete-game-3-")
    assert temporary.name.endswith(".tmp")


def test_tree_digest_has_unambiguous_file_boundaries(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    payload = b"payload"
    (first / "a").write_bytes((1).to_bytes(4, "big") + b"b" + payload)
    (second / "a").write_bytes(b"")
    (second / "b").write_bytes(payload)

    assert Storage.legacy_tree_digest(first)[0] == Storage.legacy_tree_digest(second)[0]
    assert Storage.tree_digest(first)[0] != Storage.tree_digest(second)[0]
