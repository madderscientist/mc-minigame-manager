import hashlib
import io
from pathlib import Path

import pytest

from mc_manager.app import _save_upload_file
from mc_manager.errors import ValidationError


def test_save_upload_file_streams_and_hashes(tmp_path: Path) -> None:
    content = b"resource-data" * 1000
    destination = tmp_path / "upload.bin"

    sha256, sha1 = _save_upload_file(io.BytesIO(content), destination, len(content))

    assert destination.read_bytes() == content
    assert sha256 == hashlib.sha256(content).hexdigest()
    assert sha1 == hashlib.sha1(content, usedforsecurity=False).hexdigest()


def test_save_upload_file_enforces_limit(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="大小限制"):
        _save_upload_file(io.BytesIO(b"too large"), tmp_path / "upload.bin", 4)
