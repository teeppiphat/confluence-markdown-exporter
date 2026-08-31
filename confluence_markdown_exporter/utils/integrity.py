"""Create a deterministic integrity manifest for exported artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING

from confluence_markdown_exporter.utils.export import save_file
from confluence_markdown_exporter.utils.output_safety import OutputPathRegistry

if TYPE_CHECKING:
    from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_integrity_manifest(
    output_path: Path,
    manifest_name: str,
    *,
    excluded_names: set[str] | None = None,
) -> Path:
    """Hash regular output files and atomically write a deterministic manifest."""
    root = output_path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = OutputPathRegistry.reserve(root, manifest_name, "system:manifest")
    excluded = {manifest_path.name, ".cme-export.lock", *(excluded_names or set())}
    artifacts: list[dict[str, str | int]] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name in excluded:
            continue
        relative = path.relative_to(root).as_posix()
        artifacts.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )

    payload = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": "sha256",
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    save_file(manifest_path, json.dumps(payload, indent=2, ensure_ascii=False))
    return manifest_path
