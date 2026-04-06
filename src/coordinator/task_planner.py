"""任务规划器 - Coordinator 核心"""

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from loguru import logger

from agents.base_agent import LLMProvider, create_provider
from agents.worker_pool import (
    Task,
    SubTask,
    TaskStatus,
    WorkerPool,
    WorkerConfig,
)


class TaskComplexity(Enum):
    """任务复杂度"""
    SIMPLE = "simple"      # 简单任务，单 Agent 直接执行
    PARALLEL = "parallel"  # 可并行任务，多 Worker 同时执行
    SEQUENTIAL = "sequential"  # 顺序依赖任务
    MIXED = "mixed"        # 混合任务


@dataclass
class OptimizationSuggestion:
    """优化建议"""
    type: str  # code_quality | performance | security | architecture | alternative
    title: str
    description: str
    confidence: float  # 0-1
    code_snippet: str | None = None
    priority: int = 0  # 0: low, 1: medium, 2: high


class TaskPlanner:
    """任务规划器 - 负责任务分解和协调"""
    
    def __init__(
        self,
        provider: LLMProvider,
        worker_pool: WorkerPool,
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.7,
    ):
        self.provider = provider
        self.worker_pool = worker_pool
        self.model = model
        self.temperature = temperature
        
        # 任务存储
        self._tasks: dict[str, Task] = {}
        self._task_results: dict[str, dict[str, Any]] = {}
    
    async def plan_task(
        self,
        task_description: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[Task, TaskComplexity]:
        """分析并规划任务"""
        # 使用 LLM 分析任务复杂度并生成子任务
        analysis_prompt = f"""分析以下任务，确定：
1. 任务复杂度（simple/parallel/sequential/mixed）
2. 需要拆分的子任务列表

任务：{task_description}

{json.dumps(context, ensure_ascii=False, indent=2) if context else ''}

请以 JSON 格式返回：
{{
    "complexity": "simple|parallel|sequential|mixed",
    "reasoning": "分析理由",
    "sub_tasks": [
        {{
            "description": "子任务描述",
            "dependencies": ["依赖的子任务ID，如果有的话"]
        }}
    ]
}}
"""
        
        messages = [
            {"role": "system", "content": """你是一个任务规划专家。请分析用户任务并拆分为可执行的子任务。

规则：
1. 如果任务可以并行执行多个部分，使用 parallel 模式
2. 如果子任务有依赖关系，使用 sequential 模式
3. 混合模式用于既有并行又有顺序的任务
4. 简单任务不需要拆分"""},
            {"role": "user", "content": analysis_prompt},
        ]
        
        try:
            response = await self.provider.chat(
                messages=messages,
                model=self.model,
                temperature=0.3,  # 低温度以获得更一致的输出
                max_tokens=2000,
            )
            
            # 解析响应
            content = response.content or "{}"
            
            # 提取 JSON
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(content)
            
            complexity_str = data.get("complexity", "simple")
            complexity = TaskComplexity(complexity_str.lower())
            
            # 创建子任务（先分配 ID，再解析依赖）
            raw_sub_tasks = data.get("sub_tasks", [])
            # 预先分配 ID，用于后续依赖映射
            sub_task_ids = [f"sub_{uuid.uuid4().hex[:6]}" for _ in raw_sub_tasks]

            sub_tasks = []
            for i, st_data in enumerate(raw_sub_tasks):
                raw_deps = st_data.get("dependencies", [])
                # 将 LLM 返回的依赖引用（如 "task_1", "sub_xxx", 数字等）映射到真实 ID
                resolved_deps: list[str] = []
                for dep in raw_deps:
                    dep_str = str(dep).strip()
                    # 如果已经是有效 ID（在预分配 ID 中），直接使用
                    if dep_str in sub_task_ids:
                        resolved_deps.append(dep_str)
                    else:
                        # 尝试解析为 1-based 或 0-based 索引
                        import re as _re
                        m = _re.search(r'\d+', dep_str)
                        if m:
                            idx = int(m.group())
                            # 尝试 1-based
                            if 1 <= idx <= len(sub_task_ids):
                                resolved_deps.append(sub_task_ids[idx - 1])
                            # 尝试 0-based
                            elif 0 <= idx < len(sub_task_ids):
                                resolved_deps.append(sub_task_ids[idx])
                        # 无法解析的依赖：顺序依赖于前一个子任务
                        elif i > 0:
                            resolved_deps.append(sub_task_ids[i - 1])
                sub_task = SubTask(
                    id=sub_task_ids[i],
                    description=st_data.get("description", ""),
                    dependencies=resolved_deps,
                )
                sub_tasks.append(sub_task)
            
            # 创建主任务
            task = Task(
                id=f"task_{uuid.uuid4().hex[:8]}",
                description=task_description,
                sub_tasks=sub_tasks if sub_tasks else [SubTask(
                    id=f"sub_{uuid.uuid4().hex[:6]}",
                    description=task_description,
                )],
            )
            
            self._tasks[task.id] = task
            logger.info(f"Planned task {task.id}: {len(sub_tasks)} sub-tasks, complexity={complexity.value}")
            
            return task, complexity
            
        except Exception as e:
            logger.error(f"Failed to plan task: {e}")
            # 回退到简单任务
            task = Task(
                id=f"task_{uuid.uuid4().hex[:8]}",
                description=task_description,
                sub_tasks=[SubTask(
                    id=f"sub_{uuid.uuid4().hex[:6]}",
                    description=task_description,
                )],
            )
            self._tasks[task.id] = task
            return task, TaskComplexity.SIMPLE
    
    async def execute_task(
        self,
        task: Task,
        context: dict[str, Any] | None = None,
        on_progress: Callable[[str, str, float], None] | None = None,
    ) -> str:
        """执行任务"""
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().isoformat()
        
        total_subtasks = len(task.sub_tasks)
        completed = 0
        
        def update_progress(subtask_id: str, message: str, progress: float):
            if on_progress:
                on_progress(subtask_id, message, progress)
        
        try:
            # 按依赖关系排序并执行
            if len(task.sub_tasks) == 1:
                # 简单任务
                sub_task = task.sub_tasks[0]
                sub_task.status = TaskStatus.RUNNING
                sub_task.started_at = datetime.now().isoformat()
                
                result, error = await self.worker_pool.execute_task(
                    sub_task,
                    context,
                    lambda msg: update_progress(sub_task.id, msg, 0.5),
                )
                
                sub_task.status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
                sub_task.result = result
                sub_task.error = error
                sub_task.completed_at = datetime.now().isoformat()
                
                if error:
                    task.error = error
                else:
                    task.result = result
                
                completed = 1
                update_progress(sub_task.id, "完成", 1.0)
                
            else:
                # 多子任务 - 按依赖分组执行
                # 首先执行没有依赖的任务
                ready_tasks = [st for st in task.sub_tasks if not st.dependencies]
                remaining = [st for st in task.sub_tasks if st.dependencies]
                
                all_results: dict[str, str] = {}
                
                while ready_tasks or remaining:
                    if ready_tasks:
                        # 并行执行就绪的任务
                        for st in ready_tasks:
                            st.status = TaskStatus.RUNNING
                            st.started_at = datetime.now().isoformat()
                        
                        results = await self.worker_pool.execute_parallel(
                            ready_tasks,
                            context,
                            lambda tid, msg: update_progress(tid, msg, 0.5),
                        )
                        
                        for sub_task, result, error in results:
                            sub_task.status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
                            sub_task.result = result
                            sub_task.error = error
                            sub_task.completed_at = datetime.now().isoformat()
                            
                            if result:
                                all_results[sub_task.id] = result
                            
                            completed += 1
                            progress = completed / total_subtasks
                            update_progress(sub_task.id, "完成", progress)
                        
                        ready_tasks = []
                        
                        # 更新依赖任务的就绪状态
                        still_remaining = []
                        for st in remaining:
                            # 检查依赖是否都已完成
                            deps_done = all(dep in all_results for dep in st.dependencies)
                            if deps_done:
                                ready_tasks.append(st)
                            else:
                                still_remaining.append(st)
                        remaining = still_remaining
                    else:
                        # 不应该发生，但如果发生就打破循环
                        break
                
                # 汇总结果
                if all_results:
                    task.result = self._aggregate_results(all_results)
            
            task.status = TaskStatus.COMPLETED if not task.error else TaskStatus.FAILED
            task.completed_at = datetime.now().isoformat()
            
            return task.result or task.error or "任务完成"
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now().isoformat()
            logger.error(f"Task {task.id} failed: {e}")
            return f"任务执行失败: {str(e)}"
    
    def _aggregate_results(self, results: dict[str, str]) -> str:
        """聚合多个子任务的结果"""
        parts = ["## 执行结果汇总\n"]
        
        for task_id, result in results.items():
            parts.append(f"\n### {task_id}\n{result}\n")
        
        return "\n".join(parts)
    
    async def generate_optimization_suggestions(
        self,
        task: Task,
        result: str,
        context: dict[str, Any] | None = None,
    ) -> list[OptimizationSuggestion]:
        """生成优化建议"""
        prompt = f"""分析以下任务执行结果，提供优化建议。

任务描述：{task.description}
执行结果：{result}

请分析并提供以下类型的优化建议（如果有）：
1. code_quality - 代码质量优化
2. performance - 性能优化
3. security - 安全漏洞修复
4. architecture - 架构重构建议
5. alternative - 替代方案

请以 JSON 数组格式返回：
[
    {{
        "type": "优化类型",
        "title": "建议标题",
        "description": "详细说明",
        "confidence": 0.0-1.0,
        "code_snippet": "相关代码（如果有）",
        "priority": 0-2
    }}
]

如果没有明显优化空间，返回空数组 []。
"""
        
        messages = [
            {"role": "system", "content": "你是代码优化专家。请分析任务结果并提供具体、可操作的优化建议。"},
            {"role": "user", "content": prompt},
        ]
        
        try:
            response = await self.provider.chat(
                messages=messages,
                model=self.model,
                temperature=0.3,
                max_tokens=2000,
            )
            
            content = response.content or "[]"
            
            # 提取 JSON
            json_match = re.search(r'\[[\s\S]*\]', content)
            if json_match:
                suggestions_data = json.loads(json_match.group())
            else:
                suggestions_data = json.loads(content)
            
            return [
                OptimizationSuggestion(
                    type=s.get("type", "alternative"),
                    title=s.get("title", ""),
                    description=s.get("description", ""),
                    confidence=s.get("confidence", 0.5),
                    code_snippet=s.get("code_snippet"),
                    priority=s.get("priority", 1),
                )
                for s in suggestions_data
            ]
            
        except Exception as e:
            logger.error(f"Failed to generate optimization suggestions: {e}")
            return []
    
    def get_task(self, task_id: str) -> Task | None:
        """获取任务"""
        return self._tasks.get(task_id)
    
    def list_tasks(self) -> list[dict]:
        """列出所有任务"""
        return [
            {
                "id": t.id,
                "description": t.description,
                "status": t.status.value,
                "sub_tasks_count": len(t.sub_tasks),
                "created_at": t.created_at,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
            }
            for t in self._tasks.values()
        ]


# 全局实例
_task_planner: TaskPlanner | None = None


def get_task_planner() -> TaskPlanner | None:
    return _task_planner


def init_task_planner(
    provider: LLMProvider,
    worker_pool: WorkerPool,
    model: str = "claude-sonnet-4-20250514",
) -> TaskPlanner:
    global _task_planner
    _task_planner = TaskPlanner(provider, worker_pool, model)
    return _task_planner
