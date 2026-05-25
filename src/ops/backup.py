"""Create, inspect, verify, prune, and restore MemoX backups."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

BACKUP_FORMAT = "memox-backup-v1"
METADATA_NAME = "memox-backup.json"
DEFAULT_INCLUDE = ("config.yaml", ".env", "data", "workspace")
CHUNK_SIZE = 1024 * 1024


class BackupError(RuntimeError):
    """Backup or restore operation failed."""


@dataclass(frozen=True)
class BackupEntry:
    path: str
    type: str
    size: int = 0
    sha256: str | None = None
    mode: int | None = None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_backup_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return root / "backups" / f"memox-backup-{stamp}.tar.gz"


def list_backup_archives(root: str | Path = ".") -> list[Path]:
    """Return MemoX backup archives sorted from newest to oldest."""
    backup_dir = Path(root).resolve() / "backups"
    if not backup_dir.exists():
        return []
    archives = [path for path in backup_dir.glob("memox-backup-*.tar.gz") if path.is_file()]
    return sorted(archives, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def safe_include_path(value: str | Path) -> Path:
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise BackupError(f"Unsafe include path: {value}")
    if not rel.parts:
        raise BackupError("Include path must not be empty")
    return rel


def safe_archive_path(value: str) -> PurePosixPath:
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise BackupError(f"Unsafe archive path: {value}")
    return rel


def target_path(target: Path, rel_name: str) -> Path:
    rel = safe_archive_path(rel_name)
    dest = target.joinpath(*rel.parts).resolve()
    if not dest.is_relative_to(target):
        raise BackupError(f"Archive path escapes restore target: {rel_name}")
    return dest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(fh: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
        digest.update(chunk)
    return digest.hexdigest()


def rel_name(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def collect_entries(root: Path, include: tuple[str, ...]) -> tuple[list[BackupEntry], list[str], list[dict[str, str]]]:
    entries: list[BackupEntry] = []
    missing: list[str] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in include:
        rel = safe_include_path(item)
        path = (root / rel).resolve()
        if not path.exists():
            missing.append(rel.as_posix())
            continue
        if not path.is_relative_to(root):
            raise BackupError(f"Include path escapes backup root: {item}")

        paths = [path]
        if path.is_dir():
            paths.extend(sorted(path.rglob("*")))

        for current in paths:
            rel_path = rel_name(root, current)
            if rel_path in seen:
                continue
            seen.add(rel_path)

            if current.is_symlink():
                skipped.append({"path": rel_path, "reason": "symlink"})
                continue
            if current.is_dir():
                entries.append(BackupEntry(path=rel_path, type="directory", mode=current.stat().st_mode & 0o777))
                continue
            if current.is_file():
                stat = current.stat()
                entries.append(
                    BackupEntry(
                        path=rel_path,
                        type="file",
                        size=stat.st_size,
                        sha256=sha256_file(current),
                        mode=stat.st_mode & 0o777,
                    )
                )
                continue
            skipped.append({"path": rel_path, "reason": "not a regular file"})

    entries.sort(key=lambda entry: entry.path)
    return entries, missing, skipped


def backup_metadata(
    *,
    root: Path,
    include: tuple[str, ...],
    entries: list[BackupEntry],
    missing: list[str],
    skipped: list[dict[str, str]],
) -> dict:
    return {
        "format": BACKUP_FORMAT,
        "created_at": utc_timestamp(),
        "root": str(root),
        "included": list(include),
        "missing": missing,
        "skipped": skipped,
        "entries": [asdict(entry) for entry in entries],
    }


def add_directory(tar: tarfile.TarFile, entry: BackupEntry) -> None:
    info = tarfile.TarInfo(entry.path)
    info.type = tarfile.DIRTYPE
    info.mode = entry.mode or 0o755
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    tar.addfile(info)


def add_file(tar: tarfile.TarFile, root: Path, entry: BackupEntry) -> None:
    path = root.joinpath(*PurePosixPath(entry.path).parts)
    info = tarfile.TarInfo(entry.path)
    info.size = entry.size
    info.mode = entry.mode or 0o600
    info.mtime = int(path.stat().st_mtime)
    with path.open("rb") as fh:
        tar.addfile(info, fh)


def create_backup(
    *,
    root: str | Path = ".",
    output: str | Path | None = None,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
    overwrite: bool = False,
) -> dict:
    """Create a MemoX backup archive and return its metadata."""
    root_path = Path(root).resolve()
    output_path = Path(output).resolve() if output else default_backup_path(root_path)
    if output_path.exists() and not overwrite:
        raise BackupError(f"Backup already exists: {output_path}")

    entries, missing, skipped = collect_entries(root_path, include)
    if not entries:
        raise BackupError("No backup entries found")

    metadata = backup_metadata(root=root_path, include=include, entries=entries, missing=missing, skipped=skipped)
    metadata_bytes = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent, delete=False) as tmp:
        temp_path = Path(tmp.name)

    try:
        with tarfile.open(temp_path, "w:gz") as tar:
            info = tarfile.TarInfo(METADATA_NAME)
            info.size = len(metadata_bytes)
            info.mode = 0o600
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            with tempfile.SpooledTemporaryFile() as metadata_file:
                metadata_file.write(metadata_bytes)
                metadata_file.seek(0)
                tar.addfile(info, metadata_file)

            for entry in entries:
                if entry.type == "directory":
                    add_directory(tar, entry)
                elif entry.type == "file":
                    add_file(tar, root_path, entry)
                else:
                    raise BackupError(f"Unknown backup entry type: {entry.type}")

        if output_path.exists() and overwrite:
            output_path.unlink()
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return {"archive": str(output_path), **metadata}


def validate_metadata_entries(metadata: dict) -> list[BackupEntry]:
    entries: list[BackupEntry] = []
    seen: set[str] = set()
    for raw in metadata.get("entries", []):
        path = str(raw.get("path", ""))
        safe_archive_path(path)
        if path in seen:
            raise BackupError(f"Duplicate metadata entry: {path}")
        seen.add(path)

        entry_type = raw.get("type")
        if entry_type not in {"file", "directory"}:
            raise BackupError(f"Unsupported entry type for {path}: {entry_type}")
        size = int(raw.get("size", 0))
        sha256 = raw.get("sha256")
        mode = raw.get("mode")
        if entry_type == "file" and (not sha256 or size < 0):
            raise BackupError(f"Invalid file metadata for {path}")
        entries.append(BackupEntry(path=path, type=entry_type, size=size, sha256=sha256, mode=mode))
    return entries


def read_backup_metadata(archive: str | Path) -> dict:
    """Read and validate backup metadata without verifying file checksums."""
    archive_path = Path(archive)
    with tarfile.open(archive_path, "r:gz") as tar:
        try:
            member = tar.getmember(METADATA_NAME)
        except KeyError as exc:
            raise BackupError(f"Missing {METADATA_NAME}") from exc
        extracted = tar.extractfile(member)
        if extracted is None:
            raise BackupError(f"Cannot read {METADATA_NAME}")
        metadata = json.loads(extracted.read().decode("utf-8"))

        if metadata.get("format") != BACKUP_FORMAT:
            raise BackupError(f"Unsupported backup format: {metadata.get('format')}")
        seen: set[str] = set()
        for member in tar.getmembers():
            safe_archive_path(member.name)
            if member.name in seen:
                raise BackupError(f"Duplicate archive member: {member.name}")
            seen.add(member.name)
    validate_metadata_entries(metadata)
    return metadata


def verify_backup(archive: str | Path) -> dict:
    """Verify backup metadata, members, sizes, and checksums."""
    metadata = read_backup_metadata(archive)
    entries = validate_metadata_entries(metadata)
    expected_paths = {entry.path for entry in entries} | {METADATA_NAME}

    with tarfile.open(archive, "r:gz") as tar:
        member_paths = {member.name for member in tar.getmembers()}
        extra_paths = member_paths - expected_paths
        if extra_paths:
            raise BackupError(f"Archive contains unexpected members: {', '.join(sorted(extra_paths))}")

        for entry in entries:
            try:
                member = tar.getmember(entry.path)
            except KeyError as exc:
                raise BackupError(f"Missing archive member: {entry.path}") from exc

            if entry.type == "directory":
                if not member.isdir():
                    raise BackupError(f"Expected directory member: {entry.path}")
                continue

            if not member.isfile():
                raise BackupError(f"Expected file member: {entry.path}")
            if member.size != entry.size:
                raise BackupError(f"Size mismatch for {entry.path}: {member.size} != {entry.size}")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise BackupError(f"Cannot read file member: {entry.path}")
            digest = sha256_stream(extracted)
            if digest != entry.sha256:
                raise BackupError(f"Checksum mismatch for {entry.path}")

    return {"archive": str(Path(archive).resolve()), "verified": True, **metadata}


def restore_backup(
    *,
    archive: str | Path,
    target: str | Path = ".",
    overwrite: bool = False,
) -> dict:
    """Restore a verified MemoX backup archive."""
    metadata = verify_backup(archive)
    entries = validate_metadata_entries(metadata)
    target = Path(target).resolve()

    conflicts: list[str] = []
    for entry in entries:
        dest = target_path(target, entry.path)
        if entry.type == "directory":
            if dest.exists() and not dest.is_dir():
                conflicts.append(entry.path)
        elif dest.exists() and (not overwrite or dest.is_dir()):
            conflicts.append(entry.path)

    if conflicts:
        preview = ", ".join(conflicts[:8])
        suffix = "" if len(conflicts) <= 8 else f" ... +{len(conflicts) - 8} more"
        raise BackupError(f"Restore would overwrite existing paths: {preview}{suffix}")

    restored: list[str] = []
    with tarfile.open(archive, "r:gz") as tar:
        for entry in entries:
            dest = target_path(target, entry.path)
            member = tar.getmember(entry.path)
            if entry.type == "directory":
                dest.mkdir(parents=True, exist_ok=True)
                if entry.mode is not None:
                    dest.chmod(entry.mode)
                restored.append(entry.path)
                continue

            extracted = tar.extractfile(member)
            if extracted is None:
                raise BackupError(f"Cannot read file member: {entry.path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
                temp_path = Path(tmp.name)
                while chunk := extracted.read(CHUNK_SIZE):
                    tmp.write(chunk)
            if entry.mode is not None:
                temp_path.chmod(entry.mode)
            os.replace(temp_path, dest)
            restored.append(entry.path)

    return {
        "archive": str(Path(archive).resolve()),
        "target": str(target),
        "restored": restored,
        "entry_count": len(restored),
    }


def prune_backups(
    *,
    root: str | Path = ".",
    keep: int = 14,
    dry_run: bool = False,
) -> dict:
    """Delete old MemoX backup archives, keeping the newest N archives."""
    if keep < 1:
        raise BackupError("--keep must be at least 1")
    root_path = Path(root).resolve()
    archives = list_backup_archives(root_path)
    to_delete = archives[keep:]
    deleted: list[str] = []
    for archive in to_delete:
        if not dry_run:
            archive.unlink()
        deleted.append(str(archive))
    return {
        "root": str(root_path),
        "keep": keep,
        "dry_run": dry_run,
        "archive_count_before": len(archives),
        "archive_count_after": len(archives) if dry_run else len(archives) - len(to_delete),
        "kept": [str(path) for path in archives[:keep]],
        "deleted": deleted,
    }
