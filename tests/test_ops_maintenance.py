"""Operational maintenance runner tests."""

from pathlib import Path

import pytest

from src.ops.maintenance import OpsMaintenanceRunner, run_backup_maintenance


def _write_deployment(root: Path) -> None:
    (root / "config.yaml").write_text("app:\n  name: MemoX\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "memo.txt").write_text("persistent data\n", encoding="utf-8")


def test_backup_maintenance_creates_then_skips_fresh_backup(tmp_path: Path) -> None:
    _write_deployment(tmp_path)

    created = run_backup_maintenance(
        root=tmp_path,
        include=("config.yaml", "data"),
        interval_hours=24,
        max_backups=14,
    )
    skipped = run_backup_maintenance(
        root=tmp_path,
        include=("config.yaml", "data"),
        interval_hours=24,
        max_backups=14,
    )

    assert created["ok"] is True
    assert created["action"] == "created"
    assert created["verified"] is True
    assert Path(created["archive"]).exists()
    assert skipped["ok"] is True
    assert skipped["action"] == "skipped"
    assert skipped["archive"] == created["archive"]


@pytest.mark.asyncio
async def test_maintenance_runner_run_once_records_result(tmp_path: Path) -> None:
    _write_deployment(tmp_path)
    runner = OpsMaintenanceRunner(
        root=tmp_path,
        include=("config.yaml", "data"),
        interval_hours=24,
        startup_delay_seconds=0,
        max_backups=14,
    )

    result = await runner.run_once()

    assert result["action"] == "created"
    assert runner.last_result == result
