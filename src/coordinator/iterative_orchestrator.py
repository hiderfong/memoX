# src/coordinator/iterative_orchestrator.py
"""迭代协作编排器 - 多 Agent 迭代执行主循环"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from agents.base_agent import LLMProvider, ToolRegistry
from agents.mail_bus import MailBus
from agents.sandbox import SandboxManager
from agents.worker_pool import Task, SubTask, TaskStatus, WorkerPool
from coordinator.task_planner import TaskPlanner
from tools.filesystem import ReadFileTool, WriteFileTool, ListFilesTool
from tools.shell import ShellTool
from tools.mail import SendMailTool, ReadMailTool

MAX_ITERATIONS = 50
QUALITY_THRESHOLD = 0.8


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
    ):
        self._planner = planner
        self._worker_pool = worker_pool
        self._provider = provider
        self._rag_engine = rag_engine
        self._model = model
        self._temperature = temperature
        self._sandbox_mgr = SandboxManager(base_workspace)

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
    ) -> IterationResult:
        """执行迭代协作任务，返回最终结果"""
        ctx = dict(context or {})

        # Step 1: RAG 检索注入
        await self._inject_rag_context(description, ctx, active_group_ids)

        # Step 2: 任务规划
        task, complexity = await self._planner.plan_task(description, ctx)
        logger.info(f"[Orchestrator] 任务 {task.id} 规划完成，复杂度: {complexity.value}，子任务数: {len(task.sub_tasks)}")

        # Step 3: 创建沙箱 + MailBus
        self._sandbox_mgr.create_task_workspace(task.id)
        mail_bus = MailBus(task_id=task.id)

        history: list[IterationRecord] = []
        refinement_instructions = ""
        score = 0.0
        merged_summary = ""

        for iteration in range(MAX_ITERATIONS):
            logger.info(f"[Orchestrator] 任务 {task.id} 第 {iteration + 1} 轮迭代")

            # Step 4: 为 Worker 绑定工具
            self._prepare_workers(task, mail_bus, refinement_instructions)

            # Step 5: 带依赖注入地执行子任务
            await self._execute_with_deps(task, ctx)

            # Step 6: 合并沙箱 → shared/
            merged_summary = self._merge(task)

            # Step 7: 质量评估
            score, improvements = await self._evaluate(description, merged_summary, iteration)
            history.append(IterationRecord(iteration=iteration, score=score, improvements=improvements))
            logger.info(f"[Orchestrator] 第 {iteration + 1} 轮评分: {score:.2f}")

            if score >= QUALITY_THRESHOLD:
                logger.info(f"[Orchestrator] 任务 {task.id} 质量达标，结束迭代")
                break

            # 将改进指令传入下一轮
            refinement_instructions = "\n".join(improvements)

        shared_dir = str(self._sandbox_mgr.get_shared_dir(task.id))

        return IterationResult(
            task_id=task.id,
            shared_dir=shared_dir,
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
            results = await self._rag_engine.search(
                description,
                group_ids=active_group_ids,
                top_k=3,
            )
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

            registry.register(ReadFileTool(sandbox_dir, task.id, self._sandbox_mgr))
            registry.register(WriteFileTool(sandbox_dir))
            registry.register(ListFilesTool(sandbox_dir))
            registry.register(ShellTool(cwd=sandbox_dir))
            registry.register(SendMailTool(worker.config.name, mail_bus))
            registry.register(ReadMailTool(worker.config.name, mail_bus))

            worker.tools = registry
            worker.refinement_hint = refinement_instructions or None

    async def _execute_with_deps(self, task: Task, base_context: dict) -> None:
        """按依赖顺序执行子任务，将依赖结果注入后续任务的 context"""
        from datetime import datetime

        completed: dict[str, str] = {}
        pending = list(task.sub_tasks)

        while pending:
            ready = [st for st in pending if all(d in completed for d in st.dependencies)]
            if not ready:
                logger.error(f"[Orchestrator] 循环依赖或死锁，剩余: {[st.id for st in pending]}")
                break

            for st in ready:
                st.status = TaskStatus.RUNNING
                st.started_at = datetime.now().isoformat()

            per_task_ctx = {
                st.id: {
                    **base_context,
                    "dependency_results": {d: completed[d] for d in st.dependencies},
                }
                for st in ready
            }

            results = await self._worker_pool.execute_parallel(
                ready,
                context=base_context,
                per_task_contexts=per_task_ctx,
            )

            for st, result, error in results:
                st.status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
                st.result = result
                st.error = error
                st.completed_at = datetime.now().isoformat()
                completed[st.id] = result or error or ""
                pending.remove(st)

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
                    pass

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
迭代轮次：{iteration + 1} / {MAX_ITERATIONS}

请返回 JSON：
{{
  "score": 0.0-1.0,
  "passed": true/false,
  "improvements": ["具体改进点1", "改进点2"]
}}

评分标准：
- 0.0-0.4：严重缺失，主要功能未实现
- 0.4-0.7：基本完成，但有明显不足
- 0.7-0.8：大体满足需求，有少量问题
- 0.8-1.0：高质量完成，可以接受"""

        messages = [
            {"role": "system", "content": "你是质量评估专家。只返回 JSON，不要其他内容。"},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self._provider.chat(
                messages=messages,
                model=self._model,
                temperature=0.1,
                max_tokens=500,
            )
            content = response.content or "{}"
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(content)
            return float(data.get("score", 0.5)), data.get("improvements", [])
        except Exception as e:
            logger.warning(f"[Orchestrator] 质量评估失败: {e}，默认 score=0.5")
            return 0.5, []
