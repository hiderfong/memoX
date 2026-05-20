"""Mirror operational archives to an external filesystem location."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

CHUNK_SIZE = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    if not name or name != filename:
        raise ValueError(f"Unsafe archive mirror filename: {filename}")
    return name


def _resolve_mirror_root(root: Path, mirror_dir: str | Path | None) -> Path | None:
    if mirror_dir is None:
        return None
    value = str(mirror_dir).strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _base_result(
    *,
    root: Path,
    mirror_dir: str | Path | None,
    category: str,
    filename: str | None = None,
) -> dict[str, Any]:
    mirror_root = _resolve_mirror_root(root, mirror_dir)
    return {
        "ok": True,
        "status": "skipped" if mirror_root is None else "ok",
        "action": "archive_mirror",
        "enabled": mirror_root is not None,
        "category": category,
        "filename": filename,
        "mirror_dir": str(mirror_root) if mirror_root is not None else "",
    }


def _error_result(
    *,
    root: Path,
    mirror_dir: str | Path | None,
    category: str,
    filename: str | None,
    message: str,
) -> dict[str, Any]:
    return {
        **_base_result(root=root, mirror_dir=mirror_dir, category=category, filename=filename),
        "ok": False,
        "status": "error",
        "message": message,
    }


def _atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as tmp:
        temp_path = Path(tmp.name)

    try:
        shutil.copy2(source, temp_path)
        os.replace(temp_path, destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _atomic_write_bytes(content: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())

    try:
        os.replace(temp_path, destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def mirror_archive_file(
    source: str | Path | None,
    *,
    root: str | Path,
    mirror_dir: str | Path | None,
    category: str = "backups",
) -> dict[str, Any]:
    """Copy an existing archive into ``mirror_dir/category`` with hash verification."""
    root_path = Path(root).resolve()
    source_path = Path(source).resolve() if source else None
    filename = source_path.name if source_path is not None else None
    result = _base_result(root=root_path, mirror_dir=mirror_dir, category=category, filename=filename)
    if not result["enabled"]:
        result["message"] = "Archive mirror is not configured"
        return result

    try:
        if source_path is None or not source_path.is_file():
            raise FileNotFoundError(f"Archive file not found: {source}")
        filename = _safe_filename(source_path.name)
        mirror_root = Path(result["mirror_dir"])
        destination = (mirror_root / category / filename).resolve()
        if destination == source_path:
            return {
                **result,
                "status": "warning",
                "message": "Archive mirror destination matches the source archive",
                "destination": str(destination),
                "size_bytes": source_path.stat().st_size,
                "sha256": _sha256_file(source_path),
            }

        _atomic_copy_file(source_path, destination)
        source_hash = _sha256_file(source_path)
        destination_hash = _sha256_file(destination)
        if source_hash != destination_hash:
            raise OSError("Archive mirror checksum mismatch after copy")

        return {
            **result,
            "status": "ok",
            "message": f"Mirrored archive to {destination}",
            "destination": str(destination),
            "size_bytes": destination.stat().st_size,
            "sha256": destination_hash,
        }
    except Exception as exc:
        return _error_result(
            root=root_path,
            mirror_dir=mirror_dir,
            category=category,
            filename=filename,
            message=f"Archive mirror failed: {type(exc).__name__}: {exc}",
        )


def mirror_archive_bytes(
    content: bytes,
    filename: str,
    *,
    root: str | Path,
    mirror_dir: str | Path | None,
    category: str = "diagnostics",
) -> dict[str, Any]:
    """Write generated archive bytes into ``mirror_dir/category`` with hash verification."""
    root_path = Path(root).resolve()
    result = _base_result(root=root_path, mirror_dir=mirror_dir, category=category, filename=filename)
    if not result["enabled"]:
        result["message"] = "Archive mirror is not configured"
        return result

    try:
        safe_name = _safe_filename(filename)
        mirror_root = Path(result["mirror_dir"])
        destination = (mirror_root / category / safe_name).resolve()
        _atomic_write_bytes(content, destination)
        expected_hash = _sha256_bytes(content)
        destination_hash = _sha256_file(destination)
        if expected_hash != destination_hash:
            raise OSError("Archive mirror checksum mismatch after write")

        return {
            **result,
            "status": "ok",
            "message": f"Mirrored archive to {destination}",
            "destination": str(destination),
            "size_bytes": destination.stat().st_size,
            "sha256": destination_hash,
        }
    except Exception as exc:
        return _error_result(
            root=root_path,
            mirror_dir=mirror_dir,
            category=category,
            filename=filename,
            message=f"Archive mirror failed: {type(exc).__name__}: {exc}",
        )
