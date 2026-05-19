#!/usr/bin/env python3
"""Run MemoX operational readiness checks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_restore import BackupError, create_backup, read_backup_metadata, verify_backup  # noqa: E402
from src.config import default_config_path  # noqa: E402
from src.ops.readiness import (  # noqa: E402
    CheckResult,
    duration_ms,
    overall_status,
    resolve_from_root,
    run_readiness_checks,
)


def find_latest_backup(root: Path) -> Path | None:
    backup_dir = root / "backups"
    if not backup_dir.exists():
        return None
    archives = [path for path in backup_dir.glob("memox-backup-*.tar.gz") if path.is_file()]
    if not archives:
        return None
    return max(archives, key=lambda path: (path.stat().st_mtime, path.name))


def check_latest_backup(
    root: Path,
    *,
    archive: Path | None = None,
    verify: bool = True,
    verifier: Callable[[Path], dict] = verify_backup,
    inspector: Callable[[Path], dict] = read_backup_metadata,
) -> CheckResult:
    start = time.monotonic()
    backup_path = archive or find_latest_backup(root)
    if backup_path is None:
        return CheckResult(
            name="latest_backup",
            status="warning",
            message="No backup archive found under backups/",
            duration_ms=duration_ms(start),
        )
    if not backup_path.exists():
        return CheckResult(
            name="latest_backup",
            status="error",
            message=f"Backup archive does not exist: {backup_path}",
            duration_ms=duration_ms(start),
        )

    try:
        metadata = verifier(backup_path) if verify else inspector(backup_path)
    except BackupError as exc:
        return CheckResult(
            name="latest_backup",
            status="error",
            message=f"Backup validation failed: {exc}",
            details={"archive": str(backup_path)},
            duration_ms=duration_ms(start),
        )

    action = "verified" if verify else "inspected"
    return CheckResult(
        name="latest_backup",
        status="ok",
        message=f"Latest backup {action}: {backup_path}",
        details={
            "archive": str(Path(metadata.get("archive", backup_path)).resolve()),
            "created_at": metadata.get("created_at"),
            "entries": len(metadata.get("entries", [])),
            "verified": bool(metadata.get("verified", False)),
        },
        duration_ms=duration_ms(start),
    )


def check_create_backup(root: Path) -> CheckResult:
    start = time.monotonic()
    try:
        created = create_backup(root=root)
        verified = verify_backup(created["archive"])
    except BackupError as exc:
        return CheckResult(
            name="create_backup",
            status="error",
            message=f"Backup create/verify failed: {exc}",
            duration_ms=duration_ms(start),
        )

    return CheckResult(
        name="create_backup",
        status="ok",
        message=f"Created and verified backup: {created['archive']}",
        details={
            "archive": created["archive"],
            "entries": len(verified.get("entries", [])),
            "missing": created.get("missing", []),
        },
        duration_ms=duration_ms(start),
    )


def _tail_text(value: str, limit: int = 2000) -> str:
    return value if len(value) <= limit else value[-limit:]


def run_script_check(name: str, command: list[str], *, cwd: Path, timeout: float) -> CheckResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            name=name,
            status="error",
            message=f"{name} timed out after {timeout:g}s",
            details={"stdout": _tail_text(exc.stdout or ""), "stderr": _tail_text(exc.stderr or "")},
            duration_ms=duration_ms(start),
        )

    details: dict[str, Any] = {
        "command": command,
        "exit_code": completed.returncode,
    }
    try:
        details["json"] = json.loads(completed.stdout)
    except json.JSONDecodeError:
        details["stdout_tail"] = _tail_text(completed.stdout)
    if completed.stderr:
        details["stderr_tail"] = _tail_text(completed.stderr)

    status = "ok" if completed.returncode == 0 else "error"
    return CheckResult(
        name=name,
        status=status,
        message=f"{name} completed" if status == "ok" else f"{name} failed",
        details=details,
        duration_ms=duration_ms(start),
    )


def run_ops_check(
    *,
    root: Path,
    config_path: Path,
    collection_name: str,
    verify_latest_backup: bool,
    backup_archive: Path | None,
    create_backup_first: bool,
    include_smoke: bool,
    include_frontend_smoke: bool,
    include_restore_drill: bool,
    timeout: float,
) -> dict[str, Any]:
    checks: list[CheckResult] = []
    if create_backup_first:
        checks.append(check_create_backup(root))

    readiness = run_readiness_checks(root=root, config_path=config_path, collection_name=collection_name)
    checks.extend(CheckResult(**check) for check in readiness["checks"])
    checks.append(check_latest_backup(root, archive=backup_archive, verify=verify_latest_backup))

    if include_smoke or include_frontend_smoke:
        command = [sys.executable, str(ROOT / "scripts" / "smoke_test.py")]
        if include_frontend_smoke:
            command.append("--frontend")
        checks.append(run_script_check("smoke_test", command, cwd=root, timeout=timeout))

    if include_restore_drill:
        command = [sys.executable, str(ROOT / "scripts" / "restore_drill.py"), "--timeout", str(int(timeout))]
        checks.append(run_script_check("restore_drill", command, cwd=root, timeout=timeout * 2))

    status = overall_status(checks)
    return {
        "ok": status != "error",
        "status": status,
        "root": str(root),
        "config": str(config_path),
        "checks": [asdict(check) for check in checks],
    }


def _print_human(result: dict[str, Any]) -> None:
    print(f"Status: {result['status']}")
    print(f"Root: {result['root']}")
    print(f"Config: {result['config']}")
    for check in result["checks"]:
        print(f"- [{check['status']}] {check['name']}: {check['message']} ({check['duration_ms']}ms)")
        details = check.get("details") or {}
        if check["name"] == "index_consistency":
            print(f"  summary: {details.get('summary')}")
            if details.get("issue_counts"):
                print(f"  issue_counts: {details['issue_counts']}")
        elif check["name"] in {"latest_backup", "create_backup"}:
            print(f"  archive: {details.get('archive')}")
            print(f"  entries: {details.get('entries')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="MemoX deployment root. Defaults to current directory.")
    parser.add_argument("--config", help="Config path. Relative paths are resolved from --root.")
    parser.add_argument("--collection", default="documents", help="Chroma collection name.")
    parser.add_argument("--backup", help="Backup archive to inspect/verify instead of the latest backups/*.tar.gz.")
    parser.add_argument("--no-verify-backup", action="store_true", help="Inspect latest backup metadata without checksums.")
    parser.add_argument("--create-backup", action="store_true", help="Create and verify a backup before other checks.")
    parser.add_argument("--smoke", action="store_true", help="Run isolated backend smoke test.")
    parser.add_argument("--frontend-smoke", action="store_true", help="Run isolated backend + Vite frontend smoke test.")
    parser.add_argument("--restore-drill", action="store_true", help="Run full backup/restore drill.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Timeout for optional smoke/drill checks.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as errors.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    raw_config = Path(args.config) if args.config else default_config_path()
    config_path = resolve_from_root(root, raw_config)
    backup_archive = resolve_from_root(root, args.backup) if args.backup else None

    result = run_ops_check(
        root=root,
        config_path=config_path,
        collection_name=args.collection,
        verify_latest_backup=not args.no_verify_backup,
        backup_archive=backup_archive,
        create_backup_first=args.create_backup,
        include_smoke=args.smoke,
        include_frontend_smoke=args.frontend_smoke,
        include_restore_drill=args.restore_drill,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)

    if result["status"] == "error":
        return 1
    if args.strict and result["status"] == "warning":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
