"""scheduler/runner.py 单元测试"""
import sys, os, asyncio, pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from scheduler.runner import ScheduledTaskRunner
from scheduler.cron import cron_match


class DummyStore:
    def __init__(self, tasks=None):
        self._tasks = tasks or []
        self._last_run = {}
        self._next_run = {}

    def list_scheduled_tasks(self, enabled_only=False):
        if enabled_only:
            return [t for t in self._tasks if t.get("enabled") == 1]
        return self._tasks

    def mark_scheduled_task_run(self, task_id, when):
        self._last_run[task_id] = when

    def set_scheduled_task_next_run(self, task_id, when_iso):
        self._next_run[task_id] = when_iso


class DummyOrchestrator:
    def __init__(self):
        self.calls = []

    async def run(self, description, context, active_group_ids=None):
        self.calls.append((description, context))
        return MagicMock(result_summary="ok", final_score=1.0)


def test_compute_next_every_minute():
    """*/1 * * * * 下一分钟"""
    after = datetime(2024, 7, 15, 10, 5, 0)
    nxt = ScheduledTaskRunner._compute_next("*/1 * * * *", after)
    assert nxt is not None
    assert nxt == "2024-07-15T10:06"

def test_compute_next_hourly():
    """0 * * * * 下一小时整点"""
    after = datetime(2024, 7, 15, 10, 30, 0)
    nxt = ScheduledTaskRunner._compute_next("0 * * * *", after)
    assert nxt is not None
    # isoformat with timespec="minutes" gives HH:MM without seconds
    assert nxt == "2024-07-15T11:00"


def test_compute_next_invalid_returns_none():
    after = datetime(2024, 7, 15, 10, 0)
    assert ScheduledTaskRunner._compute_next("not valid", after) is None


@pytest.mark.asyncio
async def test_start_stop():
    """Runner 启动和停止"""
    store = DummyStore()
    orch = DummyOrchestrator()
    runner = ScheduledTaskRunner(store, orch)

    runner.start()
    await asyncio.sleep(0.1)
    assert runner._task is not None
    assert not runner._task.done()

    runner.stop()
    await asyncio.sleep(0.1)
    # Task should complete after stop event is set
    # (May already be done if loop exited naturally)


@pytest.mark.asyncio
async def test_tick_fires_matching_task():
    """_tick 对符合条件的任务触发 _fire"""
    store = DummyStore([{
        "id": "task_1",
        "description": "test task",
        "cron": "* * * * *",  # every minute
        "enabled": 1,
        "last_run_at": "",
        "active_group_ids": "[]",
    }])
    orch = DummyOrchestrator()
    runner = ScheduledTaskRunner(store, orch)

    now = datetime.now().replace(second=0, microsecond=0)
    await runner._tick()

    # Give fire coroutine time to run
    await asyncio.sleep(0.2)

    assert len(orch.calls) == 1
    desc, ctx = orch.calls[0]
    assert desc == "test task"
    assert ctx["source"] == "scheduled_task"
    assert "task_1" in ctx["scheduled_task_id"]


@pytest.mark.asyncio
async def test_tick_skips_disabled_task():
    """_tick 跳过已禁用的任务"""
    store = DummyStore([{
        "id": "task_disabled",
        "description": "disabled task",
        "cron": "* * * * *",
        "enabled": 0,  # disabled
        "last_run_at": "",
        "active_group_ids": "[]",
    }])
    orch = DummyOrchestrator()
    runner = ScheduledTaskRunner(store, orch)

    await runner._tick()
    await asyncio.sleep(0.1)

    assert len(orch.calls) == 0


@pytest.mark.asyncio
async def test_tick_skips_recently_run():
    """同一分钟内不会重复触发"""
    now = datetime.now().replace(second=0, microsecond=0)
    store = DummyStore([{
        "id": "task_recent",
        "description": "recent task",
        "cron": "* * * * *",
        "enabled": 1,
        "last_run_at": now.isoformat(timespec="minutes"),  # already ran this minute
        "active_group_ids": "[]",
    }])
    orch = DummyOrchestrator()
    runner = ScheduledTaskRunner(store, orch)

    await runner._tick()
    await asyncio.sleep(0.1)

    assert len(orch.calls) == 0


@pytest.mark.asyncio
async def test_fire_handles_orchestrator_error():
    """_fire 捕获 orchestrator.run 异常并记录日志，不崩溃"""
    store = DummyStore()
    orch = AsyncMock()
    orch.run = AsyncMock(side_effect=RuntimeError("orch failed"))
    runner = ScheduledTaskRunner(store, orch)

    t = {
        "id": "task_fail",
        "description": "failing task",
        "cron": "* * * * *",
        "enabled": 1,
        "last_run_at": "",
        "active_group_ids": "[]",
    }
    await runner._fire(t, datetime.now())

    # Should not raise - error is caught internally
    # orchestrator should have been called once
    assert orch.run.called


def test_cron_match_validates_basic():
    """基本的 cron_match 功能正确性（与 scheduler/cron.py 联合测试）"""
    dt = datetime(2024, 7, 15, 10, 0)  # Monday
    assert cron_match("0 10 * * *", dt) is True
    assert cron_match("0 11 * * *", dt) is False
    assert cron_match("*/5 * * * *", dt) is True  # 10:00 is divisible by 5
