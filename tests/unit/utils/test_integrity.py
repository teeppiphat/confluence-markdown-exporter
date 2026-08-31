"""Tests for export integrity manifests."""

import hashlib
import json
from pathlib import Path

from confluence_markdown_exporter.utils.integrity import write_integrity_manifest
from confluence_markdown_exporter.utils.output_safety import OutputPathRegistry


def test_manifest_hashes_exported_files_and_excludes_system_files(tmp_path: Path) -> None:
    (tmp_path / "Space").mkdir()
    artifact = tmp_path / "Space" / "Page.md"
    artifact.write_text("hello", encoding="utf-8")
    (tmp_path / "confluence-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".cme-export.lock").touch()
    OutputPathRegistry.reset()

    manifest_path = write_integrity_manifest(
        tmp_path,
        "confluence-manifest.json",
        excluded_names={"confluence-lock.json"},
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_count"] == 1
    assert manifest["artifacts"] == [
        {
            "path": "Space/Page.md",
            "size": 5,
            "sha256": hashlib.sha256(b"hello").hexdigest(),
        }
    ]


def test_manifest_replaces_previous_version_without_hashing_itself(tmp_path: Path) -> None:
    OutputPathRegistry.reset()
    first = write_integrity_manifest(tmp_path, "manifest.json")
    OutputPathRegistry.reset()
    second = write_integrity_manifest(tmp_path, "manifest.json")
    payload = json.loads(second.read_text(encoding="utf-8"))

    assert first == second
    assert payload["artifact_count"] == 0
