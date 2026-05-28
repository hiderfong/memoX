# src/coordinator/iterative_orchestrator.py
"""迭代协作编排器 - 多 Agent 迭代执行主循环"""

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from agents.base_agent import LLMProvider, ToolRegistry
from agents.mail_bus import MailBus
from agents.sandbox import SandboxManager
from agents.worker_pool import SubTask, Task, TaskStatus, WorkerPool
from coordinator.task_planner import TaskPlanner
from tools.catalog import select_allowed_tools
from tools.database import DatabaseQueryTool
from tools.filesystem import ListFilesTool, ReadFileTool, WriteFileTool
from tools.github import GitHubCreateIssueTool, GitHubSearchTool
from tools.mail import BroadcastTool, ReadBroadcastsTool, ReadMailTool, SendMailTool
from tools.playwright_crawler import PlaywrightCrawlerTool
from tools.shell import ShellTool
from tools.web import WebFetchTool, WebSearchTool

MAX_ITERATIONS = 50
QUALITY_THRESHOLD = 0.8
SUBTASK_MAX_ATTEMPTS = 2
TaskUpdateCallback = Callable[[Task, str, dict[str, Any]], Any]


@dataclass
class IterationRecord:
    """单次迭代的评估记录"""
    iteration: int
    score: float
    improvements: list[str]


@dataclass
class IterationResult:
    """迭代执行的最终结果"""
    task_id: str
    shared_dir: str
    final_score: float
    iterations: list[IterationRecord]
    result_summary: str = ""


class IterativeOrchestrator:
    """多 Agent 迭代协作编排器"""

    def __init__(
        self,
        planner: TaskPlanner,
        worker_pool: WorkerPool,
        provider: LLMProvider,
        rag_engine: Any,
        model: str,
        temperature: float = 0.3,
        base_workspace: str | Path = "data/workspace",
        max_iterations: int = MAX_ITERATIONS,
        quality_threshold: float = QUALITY_THRESHOLD,
        subtask_max_attempts: int = SUBTASK_MAX_ATTEMPTS,
        broadcast: Any = None,
        mode: Literal["iterative", "parallel"] = "iterative",
    ):
        self._planner = planner
        self._worker_pool = worker_pool
        self._provider = provider
        self._rag_engine = rag_engine
        self._model = model
        self._temperature = temperature
        self._sandbox_mgr = SandboxManager(base_workspace)
        self._max_iterations = max_iterations
        self._quality_threshold = quality_threshold
        self._subtask_max_attempts = max(1, subtask_max_attempts)
        self._running_tasks: dict[str, asyncio.Task] = {}  # task_id → asyncio.Task
        self._cancelled: set[str] = set()
        self._broadcast = broadcast  # async callable(dict) for WebSocket broadcast
        self._pending_feedback: dict[str, asyncio.Event] = {}
        self._feedback_content: dict[str, str] = {}
        self._mode = mode
        self._multi_executor: Any = None  # P7-1: lazy init

    def cancel_task(self, task_id: str) -> bool:
        """取消正在运行的任务"""
        if task_id in self._running_tasks:
            self._cancelled.add(task_id)
            self._running_tasks[task_id].cancel()
            logger.info(f"[Orchestrator] 任务 {task_id} 已请求取消")
            return True
        return False

    def is_task_running(self, task_id: str) -> bool:
        """检查任务是否正在运行"""
        return task_id in self._running_tasks

    def list_running_tasks(self) -> list[str]:
        """列出正在运行的任务 ID"""
        return list(self._running_tasks.keys())

    def submit_feedback(self, task_id: str, feedback: str) -> bool:
        """提交用户反馈，解除等待"""
        if task_id not in self._pending_feedback:
            return False
        self._feedback_content[task_id] = feedback
        self._pending_feedback[task_id].set()
        return True

    def is_waiting_feedback(self, task_id: str) -> bool:
        """是否在等待用户反馈"""
        return task_id in self._pending_feedback and not self._pending_feedback[task_id].is_set()

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
        task_id: str | None = None,
        on_task_update: TaskUpdateCallback | None = None,
    ) -> IterationResult:
        """执行迭代协作任务，返回最终结果"""
        ctx = dict(context or {})
        resume_checkpoint = ctx.pop("_resume_checkpoint", None)
        if task_id:
            ctx["_task_id"] = task_id

        # Step 1: RAG 检索注入
        await self._inject_rag_context(description, ctx, active_group_ids)

        # Step 2: 任务规划
        if self._is_resume_checkpoint(resume_checkpoint):
            task = self._task_from_checkpoint(resume_checkpoint, description, task_id)
            complexity_value = "resume"
            logger.info(f"[Orchestrator] 任务 {task.id} 从检查点恢复，子任务数: {len(task.sub_tasks)}")
        else:
            task, complexity = await self._planner.plan_task(description, ctx)
            complexity_value = complexity.value
            logger.info(f"[Orchestrator] 任务 {task.id} 规划完成，复杂度: {complexity_value}，子任务数: {len(task.sub_tasks)}")
        await self._emit_task_update(
            on_task_update,
            task,
            "planned",
            {
                "complexity": complexity_value,
                "subtask_count": len(task.sub_tasks),
                "resumed_from_checkpoint": complexity_value == "resume",
            },
        )

        # 注册为运行中任务
        current_asyncio_task = asyncio.current_task()
        if current_asyncio_task:
            self._running_tasks[task.id] = current_asyncio_task
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().isoformat()
        await self._emit_task_update(on_task_update, task, "task_running", {})

        try:
            if self._mode == "parallel":
                return await self._run_parallel(task, description, ctx, on_task_update)
            return await self._run_iterations(
                task,
                description,
                ctx,
                on_task_update,
                preserve_completed_subtasks=complexity_value == "resume",
            )
        except asyncio.CancelledError:
            logger.warning(f"[Orchestrator] 任务 {task.id} 已被取消")
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now().isoformat()
            await self._emit_task_update(on_task_update, task, "task_cancelled", {})
            shared_dir = str(self._sandbox_mgr.get_shared_dir(task.id))
            return IterationResult(
                task_id=task.id,
                shared_dir=shared_dir,
                final_score=0.0,
                iterations=[],
                result_summary="(任务已取消)",
            )
        finally:
            self._running_tasks.pop(task.id, None)
            self._cancelled.discard(task.id)

    async def _run_iterations(
        self,
        task: Task,
        description: str,
        ctx: dict[str, Any],
        on_task_update: TaskUpdateCallback | None = None,
        preserve_completed_subtasks: bool = False,
    ) -> IterationResult:
        """迭代执行主循环（可被取消）"""
        # Step 3: 创建沙箱 + MailBus
        self._sandbox_mgr.create_task_workspace(task.id)
        mail_bus = MailBus(task_id=task.id)

        history: list[IterationRecord] = []
        refinement_instructions = ""
        score = 0.0
        merged_summary = ""

        for iteration in range(self._max_iterations):
            # 检查取消
            if task.id in self._cancelled:
                raise asyncio.CancelledError()

            logger.info(f"[Orchestrator] 任务 {task.id} 第 {iteration + 1} 轮迭代")
            await self._emit_task_update(on_task_update, task, "iteration_started", {"iteration": iteration})

            # Step 4: 为 Worker 绑定工具
            self._prepare_workers(task, mail_bus, refinement_instructions)

            # Step 5: 重置子任务状态（多轮迭代时需要）
            # 每一轮质量未达标后的 refinement 都必须重新执行子任务，否则后续轮次只会
            # 反复评估同一份低质量输出，无法真正改进。
            for st in task.sub_tasks:
                if preserve_completed_subtasks and iteration == 0 and st.status == TaskStatus.COMPLETED:
                    continue
                st.status = TaskStatus.PENDING
                st.result = None
                st.error = None
                await self._emit_task_update(
                    on_task_update,
                    task,
                    "subtask_pending",
                    {"subtask_id": st.id, "iteration": iteration},
                )

            # Step 6: 带依赖注入地执行子任务
            await self._execute_with_deps(task, ctx, on_task_update=on_task_update, iteration=iteration)

            # Step 7: 合并沙箱 → shared/
            merged_summary = self._merge(task)

            # Step 8: 质量评估
            score, improvements = await self._evaluate(description, merged_summary, iteration)
            history.append(IterationRecord(iteration=iteration, score=score, improvements=improvements))
            logger.info(f"[Orchestrator] 第 {iteration + 1} 轮评分: {score:.2f}")

            if score >= self._quality_threshold:
                logger.info(f"[Orchestrator] 任务 {task.id} 质量达标，结束迭代")
                break

            # Human-in-the-Loop: 通知前端并等待用户反馈
            if self._broadcast:
                event = asyncio.Event()
                self._pending_feedback[task.id] = event
                try:
                    await self._broadcast({
                        "type": "task_needs_input",
                        "task_id": task.id,
                        "iteration": iteration,
                        "score": score,
                        "improvements": improvements,
                    })
                    # 等待用户反馈或超时 (120 秒)
                    try:
                        await asyncio.wait_for(event.wait(), timeout=120.0)
                        user_feedback = self._feedback_content.pop(task.id, "")
                        if user_feedback:
                            refinement_instructions = user_feedback
                            logger.info(f"[Orchestrator] 收到用户反馈: {user_feedback[:100]}")
                            continue
                    except asyncio.TimeoutError:
                        logger.info("[Orchestrator] 用户反馈等待超时，使用 LLM 改进建议继续")
                finally:
                    self._pending_feedback.pop(task.id, None)

            # 将改进指令传入下一轮
            refinement_instructions = "\n".join(improvements)

        shared_dir = self._sandbox_mgr.get_shared_dir(task.id)

        # Step 9: 导出邮件通信日志到 shared/mail_log.txt
        try:
            mail_log = await mail_bus.export_log()
            (shared_dir / "mail_log.txt").write_text(mail_log, encoding="utf-8")
            logger.info(f"[Orchestrator] 邮件通信日志已写入 {shared_dir / 'mail_log.txt'}")
        except Exception as e:
            logger.warning(f"[Orchestrator] 邮件日志导出失败: {e}")

        task.status = TaskStatus.COMPLETED
        task.result = merged_summary[:2000]
        task.completed_at = datetime.now().isoformat()
        await self._emit_task_update(
            on_task_update,
            task,
            "task_completed",
            {"final_score": score, "iterations": len(history)},
        )
        return IterationResult(
            task_id=task.id,
            shared_dir=str(shared_dir),
            final_score=score,
            iterations=history,
            result_summary=merged_summary[:2000],
        )

    async def _inject_rag_context(
        self,
        description: str,
        context: dict,
        active_group_ids: list[str] | None,
    ) -> None:
        """将 RAG 检索结果注入 context"""
        if not self._rag_engine:
            return
        try:
            raw_results = await self._rag_engine.search_with_graph(
                description,
                group_ids=active_group_ids,
                top_k=3,
            )
            if isinstance(raw_results, dict):
                results = raw_results.get("search_results") or []
            else:
                results = raw_results or []
            if results:
                context["knowledge_context"] = "\n".join(
                    f"[{r.metadata.get('filename', 'doc')}] {r.content[:300]}"
                    for r in results
                )
        except Exception as e:
            logger.warning(f"[Orchestrator] RAG 检索失败: {e}")

    def _prepare_workers(
        self,
        task: Task,
        mail_bus: MailBus,
        refinement_instructions: str,
    ) -> None:
        """为每个子任务的 Worker 动态绑定沙箱工具"""
        for subtask in task.sub_tasks:
            worker = self._worker_pool.get_worker_for(subtask)
            if not worker:
                continue

            sandbox_dir = self._sandbox_mgr.get_agent_sandbox(task.id, worker.config.name)
            registry = ToolRegistry()

            # 候选工具 → 用 config.tools 做白名单过滤。空白名单 = 全部可用(兼容旧行为)。
            candidates = [
                ReadFileTool(sandbox_dir, task.id, self._sandbox_mgr),
                WriteFileTool(sandbox_dir),
                ListFilesTool(sandbox_dir),
                ShellTool(cwd=sandbox_dir),
                SendMailTool(worker.config.name, mail_bus),
                ReadMailTool(worker.config.name, mail_bus),
                BroadcastTool(worker.config.name, mail_bus),
                ReadBroadcastsTool(worker.config.name, mail_bus),
                WebSearchTool(),
                WebFetchTool(),
                DatabaseQueryTool(),
                GitHubCreateIssueTool(),
                GitHubSearchTool(),
                PlaywrightCrawlerTool(),
            ]
            selected_tools, unknown = select_allowed_tools(candidates, worker.config.tools)
            if unknown:
                logger.warning(
                    f"[Orchestrator] worker {worker.config.name} 白名单含未知工具: "
                    f"{sorted(unknown)} (已忽略)"
                )
            for tool in selected_tools:
                registry.register(tool)

            # load_skill 不受 config.tools 白名单影响 — 它由 config.skills 单独控制
            if worker.config.skills:
                from pathlib import Path

                from config import get_config
                from skills.tool import LoadSkillTool
                skills_dir = Path(get_config().knowledge_base.skills_dir)
                registry.register(LoadSkillTool(skills_dir, set(worker.config.skills)))

            worker.tools = registry
            worker.refinement_hint = refinement_instructions or None

    async def _run_parallel(
        self,
        task: Task,
        description: str,
        ctx: dict[str, Any],
        on_task_update: TaskUpdateCallback | None = None,
    ) -> IterationResult:
        """P7-1: 并行执行模式（多 Agent + LLM 结果聚合）"""
        from coordinator.multi_agent_executor import MultiAgentExecutor

        # 创建独立 MailBus
        self._sandbox_mgr.create_task_workspace(task.id)
        mail_bus = MailBus(task_id=task.id)

        # 每个任务拥有独立 MailBus，executor 也按任务创建以避免复用旧通信上下文。
        self._multi_executor = MultiAgentExecutor(
            worker_pool=self._worker_pool,
            provider=self._provider,
            mail_bus=mail_bus,
            model=self._model,
            base_workspace=self._sandbox_mgr.base_workspace,
        )

        # 注册任务
        current = asyncio.current_task()
        if current:
            self._running_tasks[task.id] = current

        try:
            result = await self._multi_executor.execute_parallel(
                task, description, ctx
            )

            # 导出通信日志
            shared_dir = self._sandbox_mgr.get_shared_dir(task.id)
            try:
                inter_log = await mail_bus.export_inter_log()
                (shared_dir / "inter_agent_log.txt").write_text(inter_log, encoding="utf-8")
            except Exception as e:
                logger.warning(f"[Orchestrator] Agent 通信日志导出失败: {e}")

            task.status = TaskStatus.COMPLETED
            task.result = result.aggregated_content[:2000]
            task.completed_at = datetime.now().isoformat()
            await self._emit_task_update(
                on_task_update,
                task,
                "task_completed",
                {"final_score": result.final_score, "subtask_count": len(result.subtask_results)},
            )
            return IterationResult(
                task_id=task.id,
                shared_dir=str(shared_dir),
                final_score=result.final_score,
                iterations=[
                    IterationRecord(
                        iteration=0,
                        score=result.final_score,
                        improvements=[f"并行执行 {len(result.subtask_results)} 个子任务"],
                    )
                ],
                result_summary=result.aggregated_content[:2000],
            )
        except asyncio.CancelledError:
            logger.warning(f"[Orchestrator] 任务 {task.id} 已被取消")
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now().isoformat()
            await self._emit_task_update(on_task_update, task, "task_cancelled", {})
            shared_dir = str(self._sandbox_mgr.get_shared_dir(task.id))
            return IterationResult(
                task_id=task.id,
                shared_dir=shared_dir,
                final_score=0.0,
                iterations=[],
                result_summary="(任务已取消)",
            )
        finally:
            self._running_tasks.pop(task.id, None)
            self._cancelled.discard(task.id)

    async def _execute_with_deps(
        self,
        task: Task,
        base_context: dict,
        on_task_update: TaskUpdateCallback | None = None,
        iteration: int = 0,
    ) -> None:
        """按依赖顺序执行子任务，将依赖结果注入后续任务的 context"""
        from datetime import datetime

        completed: dict[str, str] = {
            st.id: st.result or st.error or ""
            for st in task.sub_tasks
            if st.status == TaskStatus.COMPLETED
        }
        pending = [st for st in task.sub_tasks if st.status != TaskStatus.COMPLETED]

        while pending:
            ready = [st for st in pending if all(d in completed for d in st.dependencies)]
            if not ready:
                logger.error(f"[Orchestrator] 循环依赖或死锁，剩余: {[st.id for st in pending]}")
                break

            for st in ready:
                st.status = TaskStatus.RUNNING
                st.attempts += 1
                st.started_at = datetime.now().isoformat()
                await self._emit_task_update(
                    on_task_update,
                    task,
                    "subtask_running",
                    {"subtask_id": st.id, "attempt": st.attempts, "iteration": iteration},
                )

            per_task_ctx = {
                st.id: {
                    **base_context,
                    "dependency_results": {d: completed[d] for d in st.dependencies},
                }
                for st in ready
            }
            progress_updates: list[asyncio.Task] = []

            def handle_worker_progress(subtask_id: str, message: str) -> None:
                provider_event = self._provider_progress_event(subtask_id, message, iteration)
                if provider_event is None:
                    return
                event_type, details = provider_event
                progress_updates.append(
                    asyncio.create_task(
                        self._emit_task_update(on_task_update, task, event_type, details)
                    )
                )

            results = await self._worker_pool.execute_parallel(
                ready,
                context=base_context,
                on_progress=handle_worker_progress,
                per_task_contexts=per_task_ctx,
            )
            if progress_updates:
                await asyncio.gather(*progress_updates)

            for st, result, error in results:
                if error and st.attempts < self._subtask_max_attempts:
                    st.status = TaskStatus.PENDING
                    st.result = None
                    st.error = error
                    await self._emit_task_update(
                        on_task_update,
                        task,
                        "subtask_retry_scheduled",
                        {
                            "subtask_id": st.id,
                            "attempt": st.attempts,
                            "max_attempts": self._subtask_max_attempts,
                            "error": error,
                            "iteration": iteration,
                        },
                    )
                    continue

                st.status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
                st.result = result
                st.error = error
                st.completed_at = datetime.now().isoformat()
                completed[st.id] = result or error or ""
                pending.remove(st)
                await self._emit_task_update(
                    on_task_update,
                    task,
                    "subtask_failed" if error else "subtask_completed",
                    {
                        "subtask_id": st.id,
                        "attempt": st.attempts,
                        "error": error,
                        "iteration": iteration,
                    },
                )

            # 清理：如果有任务被标记为 RUNNING 但从未被 dispatch（即在等待队列中）
            # 则重置为 PENDING 以便下次 iteration 重试；同时从 pending 中移除
            # 已 COMPLETED/FAILED 的任务，避免在 orchestrator 的状态重置中被重新执行
            for st in pending:
                if st.status == TaskStatus.RUNNING:
                    # 尝试检测是否从未被 dispatch（worker 已空闲但任务未完成）
                    assigned = st.assigned_agent or ""
                    worker = self._worker_pool._workers.get(assigned)
                    if worker and not worker.is_busy:
                        # worker 已空闲但任务状态仍为 RUNNING，说明从未被真正 dispatch
                        st.status = TaskStatus.PENDING
                        await self._emit_task_update(
                            on_task_update,
                            task,
                            "subtask_pending",
                            {"subtask_id": st.id, "reason": "worker_idle_after_running", "iteration": iteration},
                        )
                        # 不加入 still_pending，等 orchestrator 循环的状态重置
                    # 如果 worker 仍忙碌，不处理（下次 iteration 会继续等待）
                # COMPLETED/FAILED 任务不重新加入 still_pending（它们已完成）
            # pending 在循环中会被重新赋值为 still_pending

    @staticmethod
    def _is_resume_checkpoint(checkpoint: Any) -> bool:
        return isinstance(checkpoint, dict) and isinstance(checkpoint.get("sub_tasks"), list) and bool(
            checkpoint["sub_tasks"]
        )

    @classmethod
    def _task_from_checkpoint(
        cls,
        checkpoint: dict[str, Any],
        fallback_description: str,
        fallback_task_id: str | None,
    ) -> Task:
        task = Task(
            id=str(fallback_task_id or checkpoint.get("task_id") or "task_resumed"),
            description=str(checkpoint.get("description") or fallback_description),
            sub_tasks=[
                cls._subtask_from_checkpoint(raw)
                for raw in checkpoint.get("sub_tasks", [])
                if isinstance(raw, dict)
            ],
            status=cls._task_status_from_value(checkpoint.get("status"), TaskStatus.PENDING),
            result=checkpoint.get("result"),
            error=checkpoint.get("error"),
            created_at=str(checkpoint.get("created_at") or datetime.now().isoformat()),
            started_at=checkpoint.get("started_at"),
            completed_at=checkpoint.get("completed_at"),
        )
        return task

    @classmethod
    def _subtask_from_checkpoint(cls, raw: dict[str, Any]) -> SubTask:
        subtask = SubTask(
            id=str(raw.get("id") or f"sub_{len(str(raw))}"),
            description=str(raw.get("description") or ""),
            dependencies=cls._string_list(raw.get("dependencies")),
            acceptance_criteria=cls._string_list(raw.get("acceptance_criteria")),
            status=cls._task_status_from_value(raw.get("status"), TaskStatus.PENDING),
            result=raw.get("result"),
            error=raw.get("error"),
            assigned_agent=raw.get("assigned_agent"),
            attempts=cls._safe_int(raw.get("attempts")),
            created_at=str(raw.get("created_at") or datetime.now().isoformat()),
            started_at=raw.get("started_at"),
            completed_at=raw.get("completed_at"),
        )
        return subtask

    @staticmethod
    def _task_status_from_value(value: Any, default: TaskStatus) -> TaskStatus:
        try:
            return TaskStatus(str(value))
        except ValueError:
            return default

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None]

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    async def _emit_task_update(
        on_task_update: TaskUpdateCallback | None,
        task: Task,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not on_task_update:
            return
        result = on_task_update(task, event_type, details or {})
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _provider_progress_event(
        subtask_id: str,
        message: str,
        iteration: int,
    ) -> tuple[str, dict[str, Any]] | None:
        if not isinstance(message, str):
            return None

        retry_match = re.match(
            r"^provider_retry: (?P<provider>[^/]+)/(?P<model>.*?) attempt (?P<attempt>\d+) failed: (?P<error>.*)$",
            message,
        )
        if retry_match:
            details = retry_match.groupdict()
            return (
                "provider_retry",
                {
                    "subtask_id": subtask_id,
                    "iteration": iteration,
                    "provider": details["provider"],
                    "model": details["model"],
                    "attempt": int(details["attempt"]),
                    "error": details["error"],
                },
            )

        fallback_match = re.match(
            r"^provider_fallback: (?P<from_provider>[^/]+)/(?P<from_model>.*?) -> "
            r"(?P<to_provider>[^/]+)/(?P<to_model>.*?): (?P<error>.*)$",
            message,
        )
        if fallback_match:
            details = fallback_match.groupdict()
            return (
                "provider_fallback",
                {
                    "subtask_id": subtask_id,
                    "iteration": iteration,
                    "from_provider": details["from_provider"],
                    "from_model": details["from_model"],
                    "to_provider": details["to_provider"],
                    "to_model": details["to_model"],
                    "error": details["error"],
                },
            )

        usage_match = re.match(
            r"^llm_usage: worker=(?P<worker_id>\S+) input=(?P<input_tokens>\d+) "
            r"output=(?P<output_tokens>\d+) call=(?P<call_count>\d+)$",
            message,
        )
        if usage_match:
            details = usage_match.groupdict()
            input_tokens = int(details["input_tokens"])
            output_tokens = int(details["output_tokens"])
            return (
                "llm_usage",
                {
                    "subtask_id": subtask_id,
                    "iteration": iteration,
                    "worker_id": details["worker_id"],
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "call_count": int(details["call_count"]),
                },
            )

        return None

    def _merge(self, task: Task) -> str:
        """读取所有 Agent 沙箱文件，合并到 shared/，返回摘要"""
        task_workspace = self._sandbox_mgr.base_workspace / task.id
        shared_dir = self._sandbox_mgr.get_shared_dir(task.id)

        file_contents: dict[str, str] = {}

        for agent_dir in sorted(task_workspace.iterdir()):
            if agent_dir.name == "shared":
                continue
            if not agent_dir.is_dir():
                continue
            for file_path in sorted(agent_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(task_workspace)
                try:
                    content = file_path.read_text(encoding="utf-8")
                    file_contents[str(rel)] = content
                    dest = shared_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
                except Exception:
                    logger.warning(f"[IterativeOrchestrator] 读取或写入共享文件失败: {rel}")

        if file_contents:
            parts = [f"=== {path} ===\n{content[:500]}" for path, content in file_contents.items()]
            return "\n\n".join(parts)

        # 无文件输出，回退到子任务文本结果
        parts = []
        for st in task.sub_tasks:
            if st.result:
                parts.append(f"[{st.description[:50]}]\n{st.result}")
        return "\n\n".join(parts) if parts else "(无输出)"

    async def _evaluate(
        self,
        description: str,
        merged_summary: str,
        iteration: int,
    ) -> tuple[float, list[str]]:
        """调用 LLM 对当前输出质量评分，返回 (score, improvements)"""
        prompt = f"""你是 Coordinator，评估以下任务的完成质量。

原始需求：{description}
当前输出摘要（shared/ 目录内容）：
{merged_summary[:2000]}
迭代轮次：{iteration + 1} / {self._max_iterations}

请只返回一个 JSON 对象，不要包含任何其他文字或 markdown 格式：
{{"score": 0.85, "passed": true, "improvements": []}}

评分标准：
- 0.0-0.4：严重缺失，主要功能未实现
- 0.4-0.7：基本完成，但有明显不足
- 0.7-0.8：大体满足需求，有少量问题
- 0.8-1.0：高质量完成，可以接受"""

        messages = [
            {"role": "system", "content": "你是质量评估专家。只返回纯 JSON 对象，格式为 {\"score\": 数字, \"passed\": 布尔, \"improvements\": [字符串数组]}。不要用 markdown 代码块包裹，不要添加任何解释文字。"},
            {"role": "user", "content": prompt},
        ]

        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = await self._provider.chat(
                    messages=messages,
                    model=self._model,
                    temperature=0.1,
                    max_tokens=1024,
                )
                content = (response.content or "").strip()
                if not content:
                    logger.warning(f"[Orchestrator] 质量评估返回空内容 (attempt {attempt + 1})")
                    continue

                # 去除 markdown 代码块包裹
                content = re.sub(r'^```(?:json)?\s*', '', content)
                content = re.sub(r'\s*```$', '', content)
                content = content.strip()

                # 提取 JSON 对象
                json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
                if json_match:
                    data = json.loads(json_match.group())
                else:
                    data = json.loads(content)

                score = float(data.get("score", 0.5))
                improvements = data.get("improvements", [])
                # 确保 improvements 是字符串列表
                if not isinstance(improvements, list):
                    improvements = [str(improvements)] if improvements else []
                return score, improvements
            except Exception as e:
                logger.warning(f"[Orchestrator] 质量评估解析失败 (attempt {attempt + 1}): {e}")
                continue

        logger.warning(f"[Orchestrator] 质量评估 {max_retries} 次均失败，默认 score=0.5")
        return 0.5, ["评估失败，建议检查输出完整性"]
