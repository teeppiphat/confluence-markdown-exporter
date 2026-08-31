"""Cross-process locking and safe output-path reservation."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from typing import ClassVar

from filelock import FileLock
from filelock import Timeout

if TYPE_CHECKING:
    from collections.abc import Iterator


class OutputLockError(RuntimeError):
    """Raised when another exporter already owns the output directory."""


class UnsafeOutputPathError(ValueError):
    """Raised when an export path escapes its configured output directory."""


class OutputPathCollisionError(ValueError):
    """Raised when two different artifacts resolve to the same output path."""


class OutputPathRegistry:
    """Thread-safe registry of paths claimed during one export command."""

    _owners: ClassVar[dict[str, str]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        """Clear all path claims for a new command run."""
        with cls._lock:
            cls._owners = {}

    @classmethod
    def reserve(cls, output_path: Path, path: Path | str, owner: str) -> Path:
        """Validate and reserve an artifact path beneath *output_path*."""
        resolved = resolve_output_path(output_path, path)
        key = str(resolved).casefold()
        with cls._lock:
            existing_owner = cls._owners.get(key)
            if existing_owner is not None and existing_owner != owner:
                msg = (
                    f"Output path collision at {resolved}: "
                    f"claimed by {existing_owner!r} and {owner!r}."
                )
                raise OutputPathCollisionError(msg)
            cls._owners[key] = owner
        return resolved


def resolve_output_path(output_path: Path, path: Path | str) -> Path:
    """Resolve a configured relative path and reject paths outside the output root."""
    root = output_path.expanduser().resolve()
    relative = Path(path)
    if relative.is_absolute():
        msg = f"Export path must be relative to the output directory: {relative}"
        raise UnsafeOutputPathError(msg)

    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        msg = f"Export path escapes the output directory: {relative}"
        raise UnsafeOutputPathError(msg)
    return resolved


@contextmanager
def acquire_output_lock(output_path: Path) -> Iterator[Path]:
    """Acquire an OS-level lock for one output directory without waiting."""
    root = output_path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".cme-export.lock"
    lock = FileLock(lock_path)
    try:
        lock.acquire(timeout=0)
    except Timeout as e:
        msg = (
            f"Another confluence-markdown-exporter process is already writing to {root}. "
            "Wait for it to finish or choose a different export.output_path."
        )
        raise OutputLockError(msg) from e

    try:
        yield lock_path
    finally:
        lock.release()
