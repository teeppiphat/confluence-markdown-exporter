"""Tests for cross-process locking and safe artifact paths."""

from pathlib import Path

import pytest

from confluence_markdown_exporter.utils.output_safety import OutputLockError
from confluence_markdown_exporter.utils.output_safety import OutputPathCollisionError
from confluence_markdown_exporter.utils.output_safety import OutputPathRegistry
from confluence_markdown_exporter.utils.output_safety import UnsafeOutputPathError
from confluence_markdown_exporter.utils.output_safety import acquire_output_lock
from confluence_markdown_exporter.utils.output_safety import resolve_output_path


def test_resolve_output_path_accepts_child(tmp_path: Path) -> None:
    assert resolve_output_path(tmp_path, "space/page.md") == tmp_path / "space/page.md"


@pytest.mark.parametrize("path", ["../outside.md", "/outside.md", "."])
def test_resolve_output_path_rejects_escape(tmp_path: Path, path: str) -> None:
    with pytest.raises(UnsafeOutputPathError):
        resolve_output_path(tmp_path, path)


def test_registry_rejects_different_owners_for_same_path(tmp_path: Path) -> None:
    OutputPathRegistry.reset()
    OutputPathRegistry.reserve(tmp_path, "space/page.md", "page:1")

    with pytest.raises(OutputPathCollisionError):
        OutputPathRegistry.reserve(tmp_path, "space/page.md", "page:2")


def test_registry_allows_same_owner_to_reuse_path(tmp_path: Path) -> None:
    OutputPathRegistry.reset()
    first = OutputPathRegistry.reserve(tmp_path, "space/page.md", "page:1")
    second = OutputPathRegistry.reserve(tmp_path, "space/page.md", "page:1")
    assert first == second


def test_second_output_lock_fails_without_waiting(tmp_path: Path) -> None:
    with (
        acquire_output_lock(tmp_path),
        pytest.raises(OutputLockError),
        acquire_output_lock(tmp_path),
    ):
        pytest.fail("A second process lock should never be acquired")

    with acquire_output_lock(tmp_path):
        pass
