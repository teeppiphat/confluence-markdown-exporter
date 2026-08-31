"""Tests for atomic streamed file writes."""

import stat
from pathlib import Path

import pytest

from confluence_markdown_exporter.utils.export import FileSizeMismatchError
from confluence_markdown_exporter.utils.export import save_stream


def test_save_stream_writes_chunks_without_joining_them(tmp_path: Path) -> None:
    destination = tmp_path / "attachment.bin"

    written = save_stream(destination, iter([b"abc", b"", b"def"]), expected_size=6)

    assert written == 6
    assert destination.read_bytes() == b"abcdef"


def test_save_stream_size_mismatch_preserves_existing_file(tmp_path: Path) -> None:
    destination = tmp_path / "attachment.bin"
    destination.write_bytes(b"existing")

    with pytest.raises(FileSizeMismatchError):
        save_stream(destination, iter([b"short"]), expected_size=10)

    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_stream_uses_normal_file_creation_permissions(tmp_path: Path) -> None:
    reference = tmp_path / "reference.bin"
    reference.write_bytes(b"reference")
    destination = tmp_path / "attachment.bin"

    save_stream(destination, iter([b"attachment"]), expected_size=10)

    assert stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


def test_save_stream_preserves_existing_permissions(tmp_path: Path) -> None:
    destination = tmp_path / "attachment.bin"
    destination.write_bytes(b"existing")
    destination.chmod(0o640)

    save_stream(destination, iter([b"replacement"]), expected_size=11)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o640
