#!/usr/bin/env python3
"""Create, inspect, verify, and restore MemoX deployment backups."""

from __future__ import annotations

import argparse
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


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_backup_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return root / "backups" / f"memox-backup-{stamp}.tar.gz"


def _safe_include_path(value: str | Path) -> Path:
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise BackupError(f"Unsafe include path: {value}")
    if not rel.parts:
        raise BackupError("Include path must not be empty")
    return rel


def _safe_archive_path(value: str) -> PurePosixPath:
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise BackupError(f"Unsafe archive path: {value}")
    return rel


def _target_path(target: Path, rel_name: str) -> Path:
    rel = _safe_archive_path(rel_name)
    dest = target.joinpath(*rel.parts).resolve()
    if not dest.is_relative_to(target):
        raise BackupError(f"Archive path escapes restore target: {rel_name}")
    return dest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(fh: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _rel_name(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _collect_entries(root: Path, include: tuple[str, ...]) -> tuple[list[BackupEntry], list[str], list[dict[str, str]]]:
    entries: list[BackupEntry] = []
    missing: list[str] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in include:
        rel = _safe_include_path(item)
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
            rel_name = _rel_name(root, current)
            if rel_name in seen:
                continue
            seen.add(rel_name)

            if current.is_symlink():
                skipped.append({"path": rel_name, "reason": "symlink"})
                continue
            if current.is_dir():
                entries.append(BackupEntry(path=rel_name, type="directory", mode=current.stat().st_mode & 0o777))
                continue
            if current.is_file():
                stat = current.stat()
                entries.append(
                    BackupEntry(
                        path=rel_name,
                        type="file",
                        size=stat.st_size,
                        sha256=_sha256_file(current),
                        mode=stat.st_mode & 0o777,
                    )
                )
                continue
            skipped.append({"path": rel_name, "reason": "not a regular file"})

    entries.sort(key=lambda entry: entry.path)
    return entries, missing, skipped


def _metadata(
    *,
    root: Path,
    include: tuple[str, ...],
    entries: list[BackupEntry],
    missing: list[str],
    skipped: list[dict[str, str]],
) -> dict:
    return {
        "format": BACKUP_FORMAT,
        "created_at": _utc_timestamp(),
        "root": str(root),
        "included": list(include),
        "missing": missing,
        "skipped": skipped,
        "entries": [asdict(entry) for entry in entries],
    }


def _add_directory(tar: tarfile.TarFile, entry: BackupEntry) -> None:
    info = tarfile.TarInfo(entry.path)
    info.type = tarfile.DIRTYPE
    info.mode = entry.mode or 0o755
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    tar.addfile(info)


def _add_file(tar: tarfile.TarFile, root: Path, entry: BackupEntry) -> None:
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
    output_path = Path(output).resolve() if output else _default_backup_path(root_path)
    if output_path.exists() and not overwrite:
        raise BackupError(f"Backup already exists: {output_path}")

    entries, missing, skipped = _collect_entries(root_path, include)
    if not entries:
        raise BackupError("No backup entries found")

    metadata = _metadata(root=root_path, include=include, entries=entries, missing=missing, skipped=skipped)
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
                    _add_directory(tar, entry)
                elif entry.type == "file":
                    _add_file(tar, root_path, entry)
                else:
                    raise BackupError(f"Unknown backup entry type: {entry.type}")

        if output_path.exists() and overwrite:
            output_path.unlink()
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return {"archive": str(output_path), **metadata}


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
            _safe_archive_path(member.name)
            if member.name in seen:
                raise BackupError(f"Duplicate archive member: {member.name}")
            seen.add(member.name)
    _validate_metadata_entries(metadata)
    return metadata


def _validate_metadata_entries(metadata: dict) -> list[BackupEntry]:
    entries: list[BackupEntry] = []
    seen: set[str] = set()
    for raw in metadata.get("entries", []):
        path = str(raw.get("path", ""))
        _safe_archive_path(path)
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


def verify_backup(archive: str | Path) -> dict:
    """Verify backup metadata, members, sizes, and checksums."""
    metadata = read_backup_metadata(archive)
    entries = _validate_metadata_entries(metadata)
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
            digest = _sha256_stream(extracted)
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
    entries = _validate_metadata_entries(metadata)
    target_path = Path(target).resolve()

    conflicts: list[str] = []
    for entry in entries:
        dest = _target_path(target_path, entry.path)
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
            dest = _target_path(target_path, entry.path)
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
        "target": str(target_path),
        "restored": restored,
        "entry_count": len(restored),
    }


def _print_result(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if "verified" in result:
        print(f"Verified {result['archive']} ({len(result.get('entries', []))} entries)")
    elif "restored" in result:
        print(f"Restored {result['entry_count']} entries into {result['target']}")
    else:
        print(f"Created {result['archive']} ({len(result.get('entries', []))} entries)")
        if result.get("missing"):
            print("Missing optional paths: " + ", ".join(result["missing"]))
        if result.get("skipped"):
            print(f"Skipped {len(result['skipped'])} unsupported paths")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a backup archive")
    create.add_argument("--root", default=".", help="MemoX deployment root")
    create.add_argument("--output", help="Archive path. Defaults to backups/memox-backup-<timestamp>.tar.gz")
    create.add_argument("--include", action="append", help="Relative path to include. Defaults to config.yaml, .env, data, workspace")
    create.add_argument("--overwrite", action="store_true", help="Replace an existing archive at --output")
    create.add_argument("--json", action="store_true", help="Print JSON output")

    inspect = subparsers.add_parser("inspect", help="Show backup metadata")
    inspect.add_argument("archive")
    inspect.add_argument("--json", action="store_true", help="Print JSON output")

    verify = subparsers.add_parser("verify", help="Verify backup checksums")
    verify.add_argument("archive")
    verify.add_argument("--json", action="store_true", help="Print JSON output")

    restore = subparsers.add_parser("restore", help="Restore a backup archive")
    restore.add_argument("archive")
    restore.add_argument("--target", default=".", help="Restore target directory")
    restore.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    restore.add_argument("--json", action="store_true", help="Print JSON output")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "create":
            include = tuple(args.include) if args.include else DEFAULT_INCLUDE
            result = create_backup(root=args.root, output=args.output, include=include, overwrite=args.overwrite)
            _print_result(result, args.json)
        elif args.command == "inspect":
            result = {"archive": str(Path(args.archive).resolve()), **read_backup_metadata(args.archive)}
            _print_result(result, args.json)
        elif args.command == "verify":
            result = verify_backup(args.archive)
            _print_result(result, args.json)
        elif args.command == "restore":
            result = restore_backup(archive=args.archive, target=args.target, overwrite=args.overwrite)
            _print_result(result, args.json)
        else:
            parser.error(f"Unknown command: {args.command}")
    except BackupError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
