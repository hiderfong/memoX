#!/usr/bin/env python3
"""Create, inspect, verify, prune, and restore MemoX deployment backups."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.backup import (  # noqa: E402
    BACKUP_FORMAT,
    DEFAULT_INCLUDE,
    METADATA_NAME,
    BackupEntry,
    BackupError,
    create_backup,
    list_backup_archives,
    prune_backups,
    read_backup_metadata,
    restore_backup,
    verify_backup,
)

__all__ = [
    "BACKUP_FORMAT",
    "DEFAULT_INCLUDE",
    "METADATA_NAME",
    "BackupError",
    "BackupEntry",
    "create_backup",
    "list_backup_archives",
    "prune_backups",
    "read_backup_metadata",
    "restore_backup",
    "verify_backup",
]


def _print_result(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if "verified" in result:
        print(f"Verified {result['archive']} ({len(result.get('entries', []))} entries)")
    elif "restored" in result:
        print(f"Restored {result['entry_count']} entries into {result['target']}")
    elif "archive_count_before" in result:
        action = "Would delete" if result.get("dry_run") else "Deleted"
        print(
            f"{action} {len(result.get('deleted', []))} backup archive(s); "
            f"kept {len(result.get('kept', []))}"
        )
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

    prune = subparsers.add_parser("prune", help="Delete old backup archives")
    prune.add_argument("--root", default=".", help="MemoX deployment root")
    prune.add_argument("--keep", type=int, default=14, help="Number of newest backups to keep")
    prune.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    prune.add_argument("--json", action="store_true", help="Print JSON output")

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
        elif args.command == "prune":
            result = prune_backups(root=args.root, keep=args.keep, dry_run=args.dry_run)
            _print_result(result, args.json)
        else:
            parser.error(f"Unknown command: {args.command}")
    except BackupError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
