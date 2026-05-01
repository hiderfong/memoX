"""工作流执行引擎 — P8-2

支持：
- 按 DAG 拓扑序执行
- 条件跳过（condition 字段）
- 并行执行无依赖节点
- 状态持久化（SQLite）支持 pause/resume
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger

from agents.base_agent import LLMProvider
from agents.mail_bus import MailBus
from agents.worker_pool import Task, TaskStatus, WorkerPool
from agents.sandbox import SandboxManager
from workflow.dsl import StepCondition, Workflow, WorkflowStep
from workflow.parser import resolve_template


class WorkflowRunStatus(Enum):
    """工作流运行状态"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class StepRunRecord:
    """单个步骤的运行记录"""
    step_id: str
    status: WorkflowRunStatus
    input_resolved: str = ""
    output: Any = None
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0
    retry_count: int = 0


@dataclass
class WorkflowRun:
    """工作流运行实例"""
    id: str
    workflow_name: str
    status: WorkflowRunStatus
    context: dict[str, Any]            # 运行时变量存储
    step_records: list[StepRunRecord] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    paused_at: str = ""
    completed_at: str = ""

    def get_step_record(self, step_id: str) -> StepRunRecord | None:
        return next((r for r in self.step_records if r.step_id == step_id), None)

    def get_output(self, step_id: str) -> Any:
        record = self.get_step_record(step_id)
        return record.output if record else None

    def is_step_done(self, step_id: str) -> bool:
        r = self.get_step_record(step_id)
        return r is not None and r.status in (WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)

    def mark_step_started(self, step_id: str, input_resolved: str = "") -> None:
        record = self.get_step_record(step_id)
        if record:
            record.status = WorkflowRunStatus.RUNNING
            record.started_at = datetime.now().isoformat()
            record.input_resolved = input_resolved
        else:
            self.step_records.append(StepRunRecord(
                step_id=step_id,
                status=WorkflowRunStatus.RUNNING,
                input_resolved=input_resolved,
                started_at=datetime.now().isoformat(),
            ))

    def mark_step_completed(self, step_id: str, output: Any) -> None:
        record = self.get_step_record(step_id)
        if record:
            record.status = WorkflowRunStatus.COMPLETED
            record.output = output
            record.completed_at = datetime.now().isoformat()
            record.duration_ms = _duration_ms(record.started_at, record.completed_at)

    def mark_step_failed(self, step_id: str, error: str) -> None:
        record = self.get_step_record(step_id)
        if record:
            record.status = WorkflowRunStatus.FAILED
            record.error = error
            record.completed_at = datetime.now().isoformat()
            record.duration_ms = _duration_ms(record.started_at, record.completed_at)


def _duration_ms(start: str, end: str) -> float:
    try:
        t0 = datetime.fromisoformat(start)
        t1 = datetime.fromisoformat(end)
        return (t1 - t0).total_seconds() * 1000
    except Exception:
        return 0.0


class WorkflowPersistence:
    """工作流运行状态持久化（SQLite）"""

    def __init__(self, db_path: str = "data/workflows.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_name TEXT NOT NULL,
                status TEXT NOT NULL,
                context_json TEXT NOT NULL DEFAULT '{}',
                records_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paused_at TEXT,
                completed_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def save_run(self, run: WorkflowRun) -> None:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO workflow_runs
            (id, workflow_name, status, context_json, records_json,
             created_at, updated_at, paused_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run.id,
            run.workflow_name,
            run.status.value,
            json.dumps(run.context, ensure_ascii=False),
            json.dumps([_record_to_dict(r) for r in run.step_records], ensure_ascii=False),
            run.created_at,
            run.updated_at,
            run.paused_at,
            run.completed_at,
        ))
        conn.commit()
        conn.close()

    def load_run(self, run_id: str) -> WorkflowRun | None:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return WorkflowRun(
            id=row[0],
            workflow_name=row[1],
            status=WorkflowRunStatus(row[2]),
            context=json.loads(row[3]),
            step_records=[_dict_to_record(r) for r in json.loads(row[4])],
            created_at=row[5],
            updated_at=row[6],
            paused_at=row[7] or "",
            completed_at=row[8] or "",
        )

    def list_runs(self, workflow_name: str | None = None, limit: int = 50) -> list[WorkflowRun]:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        if workflow_name:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE workflow_name=? ORDER BY updated_at DESC LIMIT ?",
                (workflow_name, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()
        return [
            WorkflowRun(
                id=r[0], workflow_name=r[1], status=WorkflowRunStatus(r[2]),
                context=json.loads(r[3]),
                step_records=[_dict_to_record(rec) for rec in json.loads(r[4])],
                created_at=r[5], updated_at=r[6], paused_at=r[7] or "", completed_at=r[8] or "",
            )
            for r in rows
        ]

    def _delete_run(self, run_id: str) -> None:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM workflow_runs WHERE id = ?", (run_id,))
        conn.commit()
        conn.close()


def _record_to_dict(r: StepRunRecord) -> dict:
    return {
        "step_id": r.step_id,
        "status": r.status.value,
        "input_resolved": r.input_resolved,
        "output": r.output,
        "error": r.error,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "duration_ms": r.duration_ms,
        "retry_count": r.retry_count,
    }


def _dict_to_record(d: dict) -> StepRunRecord:
    return StepRunRecord(
        step_id=d["step_id"],
        status=WorkflowRunStatus(d["status"]),
        input_resolved=d.get("input_resolved", ""),
        output=d.get("output"),
        error=d.get("error", ""),
        started_at=d.get("started_at", ""),
        completed_at=d.get("completed_at", ""),
        duration_ms=d.get("duration_ms", 0.0),
        retry_count=d.get("retry_count", 0),
    )


class WorkflowEngine:
    """工作流执行引擎

    用法:
        engine = WorkflowEngine(worker_pool, provider, persistence)
        run = await engine.execute(workflow, context={"query": "..."})
    """

    def __init__(
        self,
        worker_pool: WorkerPool,
        provider: LLMProvider,
        persistence: WorkflowPersistence | None = None,
    ):
        self._worker_pool = worker_pool
        self._provider = provider
        self._persistence = persistence or WorkflowPersistence()

    async def execute(
        self,
        workflow: Workflow,
        context: dict[str, Any],
        run_id: str | None = None,
        on_step_change: Any = None,  # callback(step, status)
    ) -> WorkflowRun:
        """执行工作流，返回运行记录"""
        run_id = run_id or uuid.uuid4().hex
        now = datetime.now().isoformat()

        run = WorkflowRun(
            id=run_id,
            workflow_name=workflow.name,
            status=WorkflowRunStatus.RUNNING,
            context=dict(context),
            created_at=now,
            updated_at=now,
        )
        self._persistence.save_run(run)

        try:
            # 按拓扑序执行
            ordered_steps = workflow.topological_order()
            pending = list(ordered_steps)

            while pending:
                # 收集所有可并行执行的步骤
                batch = self._get_ready_batch(pending, run, workflow)
                if not batch:
                    # 悬空依赖，全部执行
                    batch = [pending.pop(0)]

                await self._execute_batch(batch, run, workflow, context, on_step_change)
                # 移除已完成的
                pending = [s for s in pending if not run.is_step_done(s.id)]
                run.updated_at = datetime.now().isoformat()
                self._persistence.save_run(run)

            # 判断最终状态
            failed = [r for r in run.step_records if r.status == WorkflowRunStatus.FAILED]
            if failed:
                run.status = WorkflowRunStatus.FAILED
            else:
                run.status = WorkflowRunStatus.COMPLETED

        except asyncio.CancelledError:
            run.status = WorkflowRunStatus.CANCELLED
            raise

        finally:
            run.updated_at = datetime.now().isoformat()
            run.completed_at = datetime.now().isoformat()
            self._persistence.save_run(run)

        return run

    async def pause(self, run_id: str) -> WorkflowRun | None:
        run = self._persistence.load_run(run_id)
        if not run:
            return None
        run.status = WorkflowRunStatus.PAUSED
        run.paused_at = datetime.now().isoformat()
        run.updated_at = datetime.now().isoformat()
        self._persistence.save_run(run)
        return run

    async def resume(
        self,
        run_id: str,
        workflow: Workflow,
        context: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        """恢复暂停的工作流"""
        run = self._persistence.load_run(run_id)
        if not run:
            raise ValueError(f"未找到运行记录: {run_id}")
        if run.status != WorkflowRunStatus.PAUSED:
            raise ValueError(f"当前状态不可恢复: {run.status.value}")

        if context:
            run.context.update(context)
        run.status = WorkflowRunStatus.RUNNING
        run.updated_at = datetime.now().isoformat()
        self._persistence.save_run(run)

        # 重新执行未完成的步骤
        pending = [s for s in workflow.steps if not run.is_step_done(s.id)]
        ordered = workflow.topological_order()
        ordered = [s for s in ordered if not run.is_step_done(s.id)]

        while pending:
            batch = self._get_ready_batch(ordered, run, workflow)
            if not batch:
                batch = [ordered.pop(0)]
            await self._execute_batch(batch, run, workflow, run.context, None)
            pending = [s for s in pending if not run.is_step_done(s.id)]
            run.updated_at = datetime.now().isoformat()
            self._persistence.save_run(run)

        run.status = WorkflowRunStatus.COMPLETED
        run.completed_at = datetime.now().isoformat()
        run.updated_at = datetime.now().isoformat()
        self._persistence.save_run(run)
        return run

    def _get_ready_batch(
        self,
        pending: list[WorkflowStep],
        run: WorkflowRun,
        workflow: Workflow,
    ) -> list[WorkflowStep]:
        """收集所有可立即执行的步骤（无依赖或依赖已满足）"""
        step_map = {s.id: s for s in pending}

        ready = []
        for s in pending:
            deps = s.get_input_refs()
            all_deps_done = all(
                run.is_step_done(ref.split(".")[0])
                for ref in deps
                if ref.split(".")[0] in step_map
            )
            if all_deps_done:
                ready.append(s)

        # 将可以并行的步骤组成 batch（无相互依赖）
        if not ready:
            return []

        # 返回全部就绪的（上层调用负责判断并行可行性）
        return ready

    async def _execute_batch(
        self,
        batch: list[WorkflowStep],
        run: WorkflowRun,
        workflow: Workflow,
        context: dict[str, Any],
        on_step_change: Any,
    ) -> None:
        """执行一批步骤（可能并行）"""
        coros = []
        for step in batch:
            if self._should_skip_step(step, run, workflow):
                run.mark_step_started(step.id, resolve_template(step.input, run.context))
                run.mark_step_completed(step.id, "(跳过)")
                logger.info(f"[WorkflowEngine] 步骤 '{step.id}' 条件不满足，已跳过")
            else:
                coros.append(self._execute_step(step, run, workflow, context, on_step_change))

        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    step = batch[i]
                    logger.error(f"[WorkflowEngine] 步骤 '{step.id}' 异常: {r}")

    def _should_skip_step(self, step: WorkflowStep, run: WorkflowRun, workflow: Workflow) -> bool:
        """判断步骤是否应跳过"""
        if step.condition == StepCondition.ALWAYS:
            return False

        deps = step.get_input_refs()
        if not deps:
            return False

        if step.condition == StepCondition.IF_FAILED:
            # 前置步骤有失败才执行
            for ref in deps:
                target_id = ref.split(".")[0]
                record = run.get_step_record(target_id)
                if record and record.status == WorkflowRunStatus.FAILED:
                    return False
            return True

        if step.condition in (StepCondition.IF_RESULT, StepCondition.IF_RELEVANT):
            for ref in deps:
                target_id = ref.split(".")[0]
                output = run.get_output(target_id)
                if output:
                    return False
            return True

        return False

    async def _execute_step(
        self,
        step: WorkflowStep,
        run: WorkflowRun,
        workflow: Workflow,
        context: dict[str, Any],
        on_step_change: Any,
    ) -> Any:
        """执行单个步骤"""
        # 解析输入模板
        input_resolved = resolve_template(step.input, run.context)
        run.mark_step_started(step.id, input_resolved)
        self._persistence.save_run(run)

        if on_step_change:
            try:
                on_step_change(step.id, WorkflowRunStatus.RUNNING)
            except Exception as e:
                logger.warning(f"[WorkflowEngine] on_step_change callback 失败: {e}")

        # 构建 Worker 执行上下文
        step_context = {
            **context,
            **run.context,
            step.output_var: "",  # placeholder
        }

        # 将依赖结果注入 context
        deps = step.get_input_refs()
        for ref in deps:
            parts = ref.split(".")
            target_id = parts[0]
            output = run.get_output(target_id)
            if output is not None:
                step_context[target_id] = output
                if len(parts) > 1:
                    try:
                        step_context[ref] = _nested_get(output, parts[1:])
                    except Exception:
                        step_context[ref] = str(output)
                else:
                    step_context[ref] = str(output)

        step_context["task_description"] = input_resolved

        # 构造 Task 给 WorkerPool
        from agents.worker_pool import SubTask
        subtask = SubTask(
            id=step.id,
            description=input_resolved,
            worker_name=step.worker,
            dependencies=[],
            status=TaskStatus.PENDING,
        )

        retry = step.retry_on_fail
        last_error = ""
        for attempt in range(retry + 1):
            try:
                worker = self._worker_pool.get_worker_for(subtask)
                if not worker:
                    raise RuntimeError(f"未找到 worker: {step.worker}")

                result_str, error = await asyncio.wait_for(
                    self._worker_pool.execute_task(subtask, step_context),
                    timeout=float(step.timeout_seconds),
                )

                if error:
                    last_error = error
                    if attempt < retry:
                        logger.warning(f"[WorkflowEngine] 步骤 '{step.id}' 失败，{attempt+1}/{retry+1} 重试")
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    run.mark_step_failed(step.id, error)
                    run.context[f"{step.id}.error"] = error
                    self._persistence.save_run(run)
                    return error

                # 成功
                run.mark_step_completed(step.id, result_str)
                run.context[step.output_var] = result_str
                run.context[f"{step.id}.success"] = True
                self._persistence.save_run(run)

                if on_step_change:
                    try:
                        on_step_change(step.id, WorkflowRunStatus.COMPLETED)
                    except Exception:
                        pass

                return result_str

            except asyncio.TimeoutError:
                last_error = f"步骤执行超时（{step.timeout_seconds}s）"
                if attempt < retry:
                    continue
                run.mark_step_failed(step.id, last_error)
                run.context[f"{step.id}.error"] = last_error
                self._persistence.save_run(run)
                return last_error

            except Exception as e:
                last_error = str(e)
                if attempt < retry:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                run.mark_step_failed(step.id, last_error)
                run.context[f"{step.id}.error"] = last_error
                self._persistence.save_run(run)
                return last_error

        return last_error


def _nested_get(obj: Any, path: list[str]) -> Any:
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj
