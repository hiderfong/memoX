"""多 Agent 并行执行器 — P7-1

将任务拆分为多个 SubTask 并行分配给不同 Worker 执行，
结果由 ResultAggregator 用 LLM 融合为连贯答案。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from agents.base_agent import LLMProvider, ToolRegistry
from agents.inter_agent_protocol import InterAgentMessage, MessagePriority, ToolResult
from agents.mail_bus import MailBus
from agents.sandbox import SandboxManager
from agents.worker_pool import Task, TaskStatus, WorkerPool
from tools.catalog import select_allowed_tools
from tools.database import DatabaseQueryTool
from tools.filesystem import ListFilesTool, ReadFileTool, WriteFileTool
from tools.github import GitHubCreateIssueTool, GitHubSearchTool
from tools.mail import BroadcastTool, ReadBroadcastsTool, ReadMailTool, SendMailTool
from tools.playwright_crawler import PlaywrightCrawlerTool
from tools.shell import ShellTool
from tools.web import WebFetchTool, WebSearchTool


@dataclass
class SubTaskResult:
    """单个子任务的执行结果"""
    subtask_id: str
    worker_name: str
    content: str = ""
    error: str = ""
    attachments: list[ToolResult] = field(default_factory=list)
    duration_ms: float = 0.0
    success: bool = True

    def to_inter_message(self) -> InterAgentMessage:
        """将结果转换为 InterAgentMessage（广播给所有 Agent）"""
        content = f"[{self.worker_name}] 执行完成: {self.content[:500]}"
        if self.error:
            content = f"[{self.worker_name}] 执行失败: {self.error}"
        return InterAgentMessage.broadcast(
            sender=self.worker_name,
            content=content,
            attachments=self.attachments,
            priority=MessagePriority.HIGH if self.success else MessagePriority.URGENT,
        )


@dataclass
class ParallelExecutionResult:
    """并行执行的总结果"""
    task_id: str
    subtask_results: list[SubTaskResult]
    aggregated_content: str = ""
    final_score: float = 0.0
    total_duration_ms: float = 0.0


class ResultAggregator:
    """LLM 驱动的结果聚合器"""

    def __init__(self, provider: LLMProvider, model: str):
        self.provider = provider
        self.model = model

    async def aggregate(
        self,
        results: list[SubTaskResult],
        original_description: str,
        task_context: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        """用 LLM 将多个 Worker 结果融合为连贯答案

        Returns:
            (aggregated_text, confidence_score 0-1)
        """
        if not results:
            return "", 0.0

        successful = [r for r in results if r.success and r.content]
        [r for r in results if not r.success]

        # 构建聚合 prompt
        worker_outputs = []
        for r in results:
            status = "✅" if r.success else "❌"
            header = f"## {status} {r.worker_name}"
            body = r.content if r.content else f"错误: {r.error}"
            attachments = ""
            if r.attachments:
                attachment_summaries = [
                    f"[{a.tool_name}]: {a.output[:150]}..." if len(a.output) > 150 else f"[{a.tool_name}]: {a.output}"
                    for a in r.attachments if a.output
                ]
                if attachment_summaries:
                    attachments = "\n工具输出:\n  " + "\n  ".join(attachment_summaries)
            worker_outputs.append(f"{header}{attachments}\n{body}\n")

        context_note = ""
        if task_context:
            context_note = f"\n\n## 任务上下文\n{json_dumps(dict(task_context), ensure_ascii=False)[:500]}"

        prompt = f"""## 原始任务
{original_description}
{context_note}

## 各 Worker 执行结果
---
{''.join(worker_outputs)}
---

## 你的任务
1. 分析以上各 Worker 的输出，提取关键信息和结论
2. 将不同角度的结果融合为一个连贯、完整的答案
3. 标注每个结论的来源 Worker（使用 `[来源: worker_name]` 格式）
4. 如果有 Worker 执行失败，在最终答案中注明

请输出一段完整、连贯的总结（300 字以内），包含：
- 任务完成情况概述
- 各 Worker 贡献的关键信息
- 最终结论

直接输出总结，不要有其他解释性文字。"""

        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.3,
                max_tokens=1024,
            )
            content = (response.content or "").strip()

            # 简单置信度评估：成功率高 + 输出非空 → 高置信度
            success_rate = len(successful) / len(results) if results else 0
            has_substantial_output = len(content) > 50
            confidence = min(1.0, success_rate * 0.6 + (0.4 if has_substantial_output else 0))

            logger.info(
                f"[ResultAggregator] 融合 {len(results)} 个结果，"
                f"成功率 {success_rate:.0%}，置信度 {confidence:.2f}"
            )
            return content, confidence

        except Exception as e:
            logger.error(f"[ResultAggregator] LLM 聚合失败: {e}")
            # 回退：拼接所有输出
            fallback = "\n\n".join(
                f"[{r.worker_name}]: {r.content or r.error}"
                for r in results
            )
            return fallback, 0.3


def json_dumps(obj: Any, **kwargs: Any) -> str:
    import json
    return json.dumps(obj, **kwargs)


class MultiAgentExecutor:
    """多 Agent 并行执行器

    使用方式:
        executor = MultiAgentExecutor(worker_pool, provider, mail_bus)
        result = await executor.execute_parallel(task, description, context)
    """

    def __init__(
        self,
        worker_pool: WorkerPool,
        provider: LLMProvider,
        mail_bus: MailBus,
        model: str = "claude-sonnet-4-20250514",
        base_workspace: str | Path = "data/workspace",
        result_aggregator: ResultAggregator | None = None,
    ):
        self._worker_pool = worker_pool
        self._provider = provider
        self._mail_bus = mail_bus
        self._model = model
        self._sandbox_mgr = SandboxManager(base_workspace)
        self._aggregator = result_aggregator or ResultAggregator(provider, model)

    async def execute_parallel(
        self,
        task: Task,
        description: str,
        base_context: dict[str, Any] | None = None,
        refinement_instructions: str = "",
    ) -> ParallelExecutionResult:
        """并行执行所有无依赖子任务，结果通过 LLM 聚合

        流程:
        1. 过滤出无依赖的子任务（可立即执行）
        2. asyncio.gather 并行执行
        3. 每完成一个就广播 InterAgentMessage
        4. 等待所有子任务完成
        5. 调用 ResultAggregator.aggregate 融合结果
        """
        ctx = dict(base_context or {})
        if refinement_instructions:
            ctx["refinement_instructions"] = refinement_instructions

        # 准备 Worker 工具
        self._sandbox_mgr.create_task_workspace(task.id)
        self._prepare_workers(task)

        start_time = datetime.now()

        pending = list(task.sub_tasks)
        completed: dict[str, SubTaskResult] = {}
        subtask_results: list[SubTaskResult] = []

        async def run_single(st: Any, context: dict) -> SubTaskResult:
            """运行单个子任务并返回结果"""
            import time
            t0 = time.perf_counter()

            worker = self._worker_pool.get_worker_for(st)
            if not worker and not getattr(self._worker_pool, "_workers", {}):
                return SubTaskResult(
                    subtask_id=st.id,
                    worker_name="unknown",
                    error=f"No worker found for subtask {st.id}",
                    success=False,
                )
            worker_name = worker.config.name if worker else (st.assigned_agent or "unknown")

            st.status = TaskStatus.RUNNING
            st.started_at = datetime.now().isoformat()

            try:
                result_str, error = await self._worker_pool.execute_task(st, context)
                worker_name = st.assigned_agent or worker_name

                duration_ms = (time.perf_counter() - t0) * 1000
                res = SubTaskResult(
                    subtask_id=st.id,
                    worker_name=worker_name,
                    content=result_str or "",
                    error=error or "",
                    success=not error,
                    duration_ms=duration_ms,
                )
                st.status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
                st.result = result_str
                st.error = error
                st.completed_at = datetime.now().isoformat()

                # 广播结果给所有 Agent
                inter_msg = res.to_inter_message()
                await self._mail_bus.send_inter_agent(inter_msg)
                logger.info(f"[MultiAgentExecutor] {worker_name} 完成，广播: {inter_msg.to_summary()}")

                return res

            except Exception as e:
                duration_ms = (time.perf_counter() - t0) * 1000
                st.status = TaskStatus.FAILED
                st.error = str(e)
                st.completed_at = datetime.now().isoformat()
                return SubTaskResult(
                    subtask_id=st.id,
                    worker_name=worker_name,
                    error=str(e),
                    success=False,
                    duration_ms=duration_ms,
                )

        # 按依赖批次执行：同一批次并行，下一批拿到 dependency_results。
        while pending:
            ready_subtasks = [
                st for st in pending
                if all(dep in completed for dep in st.dependencies)
            ]
            if not ready_subtasks:
                error = f"Cyclic or unsatisfied dependencies: {[st.id for st in pending]}"
                logger.error(f"[MultiAgentExecutor] {error}")
                for st in pending:
                    st.status = TaskStatus.FAILED
                    st.error = error
                    st.completed_at = datetime.now().isoformat()
                    res = SubTaskResult(
                        subtask_id=st.id,
                        worker_name=st.assigned_agent or "unknown",
                        error=error,
                        success=False,
                    )
                    subtask_results.append(res)
                    completed[st.id] = res
                break

            logger.info(
                f"[MultiAgentExecutor] 启动 {len(ready_subtasks)} 个并行子任务: "
                f"{[st.id for st in ready_subtasks]}"
            )
            contexts = {
                st.id: {
                    **ctx,
                    "dependency_results": {
                        dep: completed[dep].content or completed[dep].error
                        for dep in st.dependencies
                    },
                }
                for st in ready_subtasks
            }
            coros = [run_single(st, contexts[st.id]) for st in ready_subtasks]
            results = await asyncio.gather(*coros, return_exceptions=True)

            for st, r in zip(ready_subtasks, results, strict=False):
                result: SubTaskResult
                if isinstance(r, BaseException):
                    logger.error(f"[MultiAgentExecutor] 子任务异常: {r}")
                    result = SubTaskResult(
                        subtask_id=st.id,
                        worker_name="unknown",
                        error=str(r),
                        success=False,
                    )
                else:
                    result = r
                subtask_results.append(result)
                completed[st.id] = result
                pending.remove(st)

        total_duration_ms = (datetime.now() - start_time).total_seconds() * 1000

        # 阶段 2: LLM 聚合
        aggregated, confidence = await self._aggregator.aggregate(
            subtask_results, description, ctx
        )

        return ParallelExecutionResult(
            task_id=task.id,
            subtask_results=subtask_results,
            aggregated_content=aggregated,
            final_score=confidence,
            total_duration_ms=total_duration_ms,
        )

    def _prepare_workers(self, task: Task) -> None:
        """为每个子任务绑定工具（从 IterativeOrchestrator 复制逻辑）"""
        from config import get_config
        from skills.tool import LoadSkillTool

        workers = list(getattr(self._worker_pool, "_workers", {}).values())

        for worker in workers:
            sandbox_dir = self._sandbox_mgr.get_agent_sandbox(task.id, worker.config.name)

            registry = ToolRegistry()
            candidates = [
                ReadFileTool(sandbox_dir, task.id, self._sandbox_mgr),
                WriteFileTool(sandbox_dir),
                ListFilesTool(sandbox_dir),
                ShellTool(cwd=sandbox_dir),
                SendMailTool(worker.config.name, self._mail_bus),
                ReadMailTool(worker.config.name, self._mail_bus),
                BroadcastTool(worker.config.name, self._mail_bus),
                ReadBroadcastsTool(worker.config.name, self._mail_bus),
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
                    f"[MultiAgentExecutor] worker {worker.config.name} 白名单含未知工具: "
                    f"{sorted(unknown)} (已忽略)"
                )
            for tool in selected_tools:
                registry.register(tool)

            if worker.config.skills:
                skills_dir = Path(get_config().knowledge_base.skills_dir)
                registry.register(LoadSkillTool(skills_dir, set(worker.config.skills)))

            worker.tools = registry
