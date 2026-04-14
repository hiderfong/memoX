"""定时任务运行器：每分钟扫描 scheduled_tasks，触发已到期且启用的任务。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from loguru import logger

from .cron import cron_match, next_run_after


class ScheduledTaskRunner:
    """基于 asyncio 的后台循环，每 60s 扫描一次。"""

    def __init__(self, store: Any, orchestrator: Any):
        self._store = store
        self._orchestrator = orchestrator
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduled_task_runner")
        logger.info("[Scheduler] 定时任务运行器已启动")

    def stop(self) -> None:
        self._stop.set()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"[Scheduler] tick 异常: {type(e).__name__}: {e}")
            # 对齐到下一分钟的 0 秒，避免漂移
            now = datetime.now()
            sleep_s = 60 - now.second + 0.1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                return
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now().replace(second=0, microsecond=0)
        tasks = self._store.list_scheduled_tasks(enabled_only=True)
        for t in tasks:
            cron = t.get("cron") or ""
            if not cron:
                continue
            last_run = t.get("last_run_at")
            # 同一分钟只触发一次
            if last_run and last_run.startswith(now.isoformat(timespec="minutes")):
                continue
            if not cron_match(cron, now):
                continue
            asyncio.create_task(self._fire(t, now))

    async def _fire(self, t: dict, when: datetime) -> None:
        tid = t["id"]
        description = t.get("description") or ""
        active_group_ids = None
        try:
            raw = t.get("active_group_ids")
            if raw:
                active_group_ids = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            active_group_ids = None

        logger.info(f"[Scheduler] 触发定时任务 {tid}: {description[:60]}")
        # 先写入 last_run_at，避免同一分钟二次触发
        self._store.mark_scheduled_task_run(tid, when.isoformat(timespec="minutes"))
        self._store.set_scheduled_task_next_run(
            tid, self._compute_next(t.get("cron") or "", when)
        )

        try:
            await self._orchestrator.run(
                description=description,
                context={"source": "scheduled_task", "scheduled_task_id": tid},
                active_group_ids=active_group_ids,
            )
            logger.info(f"[Scheduler] 定时任务 {tid} 执行完成")
        except Exception as e:
            logger.error(f"[Scheduler] 定时任务 {tid} 执行失败: {type(e).__name__}: {e}")

    @staticmethod
    def _compute_next(cron: str, after: datetime) -> str | None:
        nxt = next_run_after(cron, after)
        return nxt.isoformat(timespec="minutes") if nxt else None


_runner: ScheduledTaskRunner | None = None


def init_runner(store: Any, orchestrator: Any) -> ScheduledTaskRunner:
    global _runner
    _runner = ScheduledTaskRunner(store, orchestrator)
    return _runner


def get_runner() -> ScheduledTaskRunner | None:
    return _runner
