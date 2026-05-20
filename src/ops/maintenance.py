"""Background operational maintenance tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from src.ops.backup import (
    BackupError,
    create_backup,
    list_backup_archives,
    prune_backups,
    read_backup_metadata,
    verify_backup,
)


def _parse_backup_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_backup_age_seconds(root: Path) -> float | None:
    archives = list_backup_archives(root)
    if not archives:
        return None
    metadata = read_backup_metadata(archives[0])
    created_at = _parse_backup_timestamp(metadata.get("created_at"))
    if created_at is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())


def run_backup_maintenance(
    *,
    root: Path,
    include: tuple[str, ...],
    interval_hours: float,
    max_backups: int,
) -> dict[str, Any]:
    """Create a backup if due, verify it, and prune old local archives."""
    root = root.resolve()
    before = list_backup_archives(root)
    created: dict[str, Any] | None = None
    verified: dict[str, Any] | None = None

    try:
        age_seconds = latest_backup_age_seconds(root)
        due = age_seconds is None or age_seconds >= interval_hours * 3600
        if due:
            created = create_backup(root=root, include=include)
            verified = verify_backup(created["archive"])
            action = "created"
            message = f"Created and verified backup: {created['archive']}"
        else:
            action = "skipped"
            message = "Latest backup is still fresh"

        pruned = prune_backups(root=root, keep=max_backups)
        return {
            "ok": True,
            "status": "ok",
            "action": action,
            "message": message,
            "root": str(root),
            "archive": created["archive"] if created else (str(before[0]) if before else None),
            "entries": len(verified.get("entries", [])) if verified else None,
            "verified": bool(verified),
            "age_seconds": age_seconds,
            "archive_count_before": len(before),
            "archive_count_after": pruned["archive_count_after"],
            "deleted": pruned["deleted"],
        }
    except BackupError as exc:
        return {
            "ok": False,
            "status": "error",
            "action": "error",
            "message": str(exc),
            "root": str(root),
            "archive_count_before": len(before),
        }


class OpsMaintenanceRunner:
    """Periodic in-process maintenance runner for operational backups."""

    def __init__(
        self,
        *,
        root: Path,
        include: tuple[str, ...],
        interval_hours: float,
        startup_delay_seconds: float,
        max_backups: int,
    ) -> None:
        self._root = root
        self._include = include
        self._interval_hours = interval_hours
        self._startup_delay_seconds = startup_delay_seconds
        self._max_backups = max_backups
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_result: dict[str, Any] | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="ops_maintenance_runner")
        logger.info("[Ops] 后台运维维护任务已启动")

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> dict[str, Any]:
        result = await asyncio.to_thread(
            run_backup_maintenance,
            root=self._root,
            include=self._include,
            interval_hours=self._interval_hours,
            max_backups=self._max_backups,
        )
        self.last_result = result
        if result.get("ok"):
            logger.info(f"[Ops] 备份维护完成: {result.get('message')}")
        else:
            logger.error(f"[Ops] 备份维护失败: {result.get('message')}")
        return result

    async def _loop(self) -> None:
        if self._startup_delay_seconds > 0:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._startup_delay_seconds)
                return
            except asyncio.TimeoutError:
                pass

        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.error(f"[Ops] 维护 tick 异常: {type(exc).__name__}: {exc}")

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_hours * 3600)
                return
            except asyncio.TimeoutError:
                pass


_runner: OpsMaintenanceRunner | None = None


def init_maintenance_runner(
    *,
    root: Path,
    include: tuple[str, ...],
    interval_hours: float,
    startup_delay_seconds: float,
    max_backups: int,
) -> OpsMaintenanceRunner:
    global _runner
    _runner = OpsMaintenanceRunner(
        root=root,
        include=include,
        interval_hours=interval_hours,
        startup_delay_seconds=startup_delay_seconds,
        max_backups=max_backups,
    )
    return _runner


def get_maintenance_runner() -> OpsMaintenanceRunner | None:
    return _runner
