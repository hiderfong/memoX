"""Operational archive mirror tests."""

from __future__ import annotations

from pathlib import Path

from src.ops.archive_mirror import mirror_archive_bytes, mirror_archive_file


def test_mirror_archive_file_copies_and_verifies_file(tmp_path: Path) -> None:
    source = tmp_path / "backups" / "memox-backup-test.tar.gz"
    source.parent.mkdir()
    source.write_bytes(b"backup-bytes")

    result = mirror_archive_file(source, root=tmp_path, mirror_dir=tmp_path / "external")

    destination = tmp_path / "external" / "backups" / source.name
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["destination"] == str(destination)
    assert result["size_bytes"] == len(b"backup-bytes")
    assert destination.read_bytes() == b"backup-bytes"


def test_mirror_archive_bytes_writes_relative_target(tmp_path: Path) -> None:
    result = mirror_archive_bytes(
        b"diagnostic-bytes",
        "memox-diagnostics-test.zip",
        root=tmp_path,
        mirror_dir="mirror",
    )

    destination = tmp_path / "mirror" / "diagnostics" / "memox-diagnostics-test.zip"
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["destination"] == str(destination)
    assert destination.read_bytes() == b"diagnostic-bytes"


def test_mirror_is_skipped_without_target(tmp_path: Path) -> None:
    result = mirror_archive_bytes(b"diagnostic-bytes", "memox-diagnostics-test.zip", root=tmp_path, mirror_dir="")

    assert result["ok"] is True
    assert result["status"] == "skipped"
    assert result["enabled"] is False


def test_mirror_rejects_unsafe_generated_filename(tmp_path: Path) -> None:
    result = mirror_archive_bytes(b"diagnostic-bytes", "../diagnostics.zip", root=tmp_path, mirror_dir=tmp_path / "mirror")

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "Unsafe archive mirror filename" in result["message"]
