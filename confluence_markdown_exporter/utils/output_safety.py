"""Cross-process locking and safe output-path reservation."""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from typing import ClassVar

from filelock import FileLock
from filelock import Timeout

from confluence_markdown_exporter.utils.export import limit_path_component_bytes

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


class PagePathRegistry:
    """Assign stable, unique relative paths to pages within one command.

    Human-readable paths remain unchanged for the first owner. If another page
    resolves to the same path, its immutable Confluence page ID is appended.
    Existing lockfile owners are loaded first so retries never rename files that
    were successfully exported by an earlier run.
    """

    _paths_by_page: ClassVar[dict[tuple[str, int], Path]] = {}
    _owners_by_path: ClassVar[dict[str, tuple[str, int]]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._paths_by_page = {}
            cls._owners_by_path = {}

    @classmethod
    def preload(cls, base_url: str, page_id: int, path: Path | str) -> None:
        """Reserve an existing lockfile path before new pages are resolved."""
        identity = (base_url, page_id)
        relative = Path(path)
        with cls._lock:
            cls._paths_by_page[identity] = relative
            cls._owners_by_path[str(relative).casefold()] = identity

    @classmethod
    def resolve(cls, base_url: str, page_id: int, candidate: Path) -> Path:
        """Return a stable unique path, adding ``~<page_id>`` on collision."""
        identity = (base_url, page_id)
        candidate_key = str(candidate).casefold()
        with cls._lock:
            existing = cls._paths_by_page.get(identity)
            if existing is not None:
                # The same candidate means either a repeated lookup or an
                # existing lockfile path. A different candidate represents a
                # moved/renamed page and must be resolved afresh.
                suffixed_name = f"{candidate.stem}~{page_id}{candidate.suffix}"
                if existing == candidate or (
                    existing.parent == candidate.parent and existing.name == suffixed_name
                ):
                    return existing

            owner = cls._owners_by_path.get(candidate_key)
            effective = candidate
            if owner is not None and owner != identity:
                effective = limit_path_component_bytes(
                    candidate.with_name(
                        f"{candidate.stem}~{page_id}{candidate.suffix}"
                    )
                )
                key = str(effective).casefold()
                # Page IDs are unique per organisation, but retain a fallback
                # for unusual custom templates or cross-org exports.
                if cls._owners_by_path.get(key) not in (None, identity):
                    digest = hashlib.sha256(
                        f"{base_url}:{page_id}".encode()
                    ).hexdigest()[:12]
                    effective = limit_path_component_bytes(
                        candidate.with_name(
                            f"{candidate.stem}~{page_id}-{digest}{candidate.suffix}"
                        )
                    )

            cls._paths_by_page[identity] = effective
            cls._owners_by_path[str(effective).casefold()] = identity
            return effective


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
def acquire_output_lock(output_path: Path, *, timeout: float = 0) -> Iterator[Path]:
    """Acquire an OS-level lock for one output directory within *timeout* seconds.

    A negative timeout waits indefinitely, which detached queue workers use so
    they remain queued behind an already-running foreground export.
    """
    root = output_path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".cme-export.lock"
    lock = FileLock(lock_path)
    try:
        lock.acquire(timeout=timeout)
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
