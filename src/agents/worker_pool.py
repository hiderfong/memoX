"""Worker Agent 池 - 多 Agent 并行执行"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from loguru import logger

from .base_agent import (
    BaseTool,
    ToolRegistry,
    LLMProvider,
    LLMResponse,
    create_provider,
)


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SubTask:
    """子任务"""
    id: str
    description: str
    dependencies: list[str] = field(default_factory=list)  # 依赖的任务 ID
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    assigned_agent: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class Task:
    """主任务"""
    id: str
    description: str
    sub_tasks: list[SubTask] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class WorkerConfig:
    """Worker Agent 配置"""
    name: str
    provider_type: str
    api_key: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    max_iterations: int = 20
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    
    @classmethod
    def from_template(cls, name: str, template: dict, api_keys: dict[str, str]) -> "WorkerConfig":
        """从模板创建配置"""
        provider = template.get("provider", "anthropic")
        api_key = api_keys.get(provider, "")
        
        return cls(
            name=name,
            provider_type=provider,
            api_key=api_key,
            model=template.get("model", "claude-sonnet-4-20250514"),
            temperature=template.get("temperature", 0.7),
            max_tokens=template.get("max_tokens", 4096),
            tools=template.get("tools", []),
            skills=template.get("skills", []),
        )


class WorkerAgent:
    """Worker Agent"""
    
    def __init__(self, config: WorkerConfig, tools: ToolRegistry | None = None, provider: LLMProvider | None = None):
        self.config = config
        self.id = config.name
        
        # 使用传入的 Provider 或创建新的
        self.provider: LLMProvider = provider or create_provider(
            config.provider_type,
            config.api_key,
        )
        
        # 创建工具注册表
        self.tools = tools or ToolRegistry()
        
        # 状态
        self._running = False
        self._current_task_id: str | None = None
    
    @property
    def is_busy(self) -> bool:
        """是否繁忙"""
        return self._running
    
    async def execute_task(
        self,
        task: SubTask,
        context: dict[str, Any] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[str, str | None]:
        """执行子任务"""
        self._running = True
        self._current_task_id = task.id
        
        logger.info(f"Worker {self.id} starting task {task.id}: {task.description[:50]}...")
        
        try:
            # 构建系统提示
            system_prompt = self._build_system_prompt()
            
            # 构建用户消息
            user_content = task.description
            if context:
                context_str = json.dumps(context, ensure_ascii=False, indent=2)
                user_content = f"""上下文信息：
```json
{context_str}
```

任务：
{task.description}"""
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            # 执行 Agent 循环
            result = await self._run_agent_loop(messages, on_progress)
            
            self._running = False
            self._current_task_id = None
            
            logger.info(f"Worker {self.id} completed task {task.id}")
            return result, None
            
        except Exception as e:
            error_msg = f"Error: {type(e).__name__}: {str(e)}"
            logger.error(f"Worker {self.id} failed task {task.id}: {e}")
            
            self._running = False
            self._current_task_id = None
            
            return "", error_msg
    
    async def _run_agent_loop(
        self,
        messages: list[dict],
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Agent 执行循环"""
        max_iterations = self.config.max_iterations
        
        for iteration in range(max_iterations):
            # 调用 LLM
            response = await self.provider.chat(
                messages=messages,
                model=self.config.model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            
            # 检查是否有工具调用
            if response.has_tool_calls:
                tool_results = []
                
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.debug(f"Executing tool: {tool_call.name}({args_str})")
                    
                    try:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        tool_results.append((tool_call, result))
                        
                        if on_progress:
                            on_progress(f"🔧 {tool_call.name}: {str(result)[:100]}...")
                    except Exception as e:
                        error_result = f"Error: {type(e).__name__}: {str(e)}"
                        tool_results.append((tool_call, error_result))
                
                # 添加助手消息和工具结果
                messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [tc.to_dict() for tc in response.tool_calls],
                })
                
                for tool_call, result in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": str(result),
                    })
                
                continue
            
            # 没有工具调用，返回结果
            if response.content:
                if on_progress:
                    on_progress(f"✅ {response.content[:100]}...")
                return response.content
            
            # 无内容
            return "Task completed but no content generated."
        
        return "Task reached maximum iterations without completion."
    
    def _build_system_prompt(self) -> str:
        """构建系统提示"""
        tool_defs = "\n".join([
            f"- **{t['function']['name']}**: {t['function']['description']}"
            for t in self.tools.get_definitions()
        ])
        
        skills_info = ""
        if self.config.skills:
            skills_info = f"\n\n## 已启用的技能\n{', '.join(self.config.skills)}"
        
        return f"""# Worker Agent: {self.config.name}

你是一个智能助手，负责执行分配给你的任务。

## 可用工具
{tool_defs or '无工具可用'}

## 工作目录
./workspace

## 要求
1. 专注于完成分配的任务
2. 使用工具时提供必要的参数
3. 如果遇到问题，尝试替代方案
4. 完成后返回简洁的结果摘要
{skills_info}
"""


class WorkerPool:
    """Worker Agent 池"""
    
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self._workers: dict[str, WorkerAgent] = {}
        self._available: asyncio.Queue[str] = asyncio.Queue()
        self._running_tasks: dict[str, asyncio.Task] = {}
        
        # 任务队列
        self._task_queue: asyncio.Queue[tuple[SubTask, str | None, Callable]] = asyncio.Queue()
        
        # 主任务存储
        self._tasks: dict[str, Task] = {}
    
    def register_worker(self, worker: WorkerAgent) -> None:
        """注册 Worker"""
        self._workers[worker.id] = worker
        logger.info(f"Registered worker: {worker.id}")
    
    def unregister_worker(self, worker_id: str) -> None:
        """注销 Worker"""
        if worker_id in self._workers:
            del self._workers[worker_id]
            logger.info(f"Unregistered worker: {worker_id}")
    
    def list_workers(self) -> list[dict]:
        """列出所有 Worker"""
        return [
            {
                "id": w.id,
                "busy": w.is_busy,
                "model": w.config.model,
            }
            for w in self._workers.values()
        ]
    
    def get_available_worker(self) -> WorkerAgent | None:
        """获取空闲的 Worker"""
        for worker in self._workers.values():
            if not worker.is_busy:
                return worker
        return None
    
    async def execute_task(
        self,
        task: SubTask,
        context: dict[str, Any] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[str, str | None]:
        """执行单个任务（自动分配 Worker）"""
        worker = self.get_available_worker()
        
        if not worker:
            # 所有 Worker 都忙，等待
            logger.warning("All workers busy, waiting for available worker...")
            # 等待任意 Worker 空闲
            while True:
                await asyncio.sleep(1)
                worker = self.get_available_worker()
                if worker:
                    break
        
        task.assigned_agent = worker.id
        return await worker.execute_task(task, context, on_progress)
    
    async def execute_parallel(
        self,
        tasks: list[SubTask],
        context: dict[str, Any] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> list[tuple[SubTask, str | None, str | None]]:
        """并行执行多个任务"""
        async def run_task(task: SubTask) -> tuple[str, str | None, str | None]:
            progress_callback = None
            if on_progress:
                def callback(msg: str):
                    on_progress(task.id, msg)
                progress_callback = callback
            
            result, error = await self.execute_task(task, context, progress_callback)
            return task.id, result, error
        
        # 使用 asyncio.gather 并行执行
        coroutines = [run_task(task) for task in tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        
        # 处理结果
        output: list[tuple[SubTask, str | None, str | None]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                output.append((tasks[i], None, str(result)))
            else:
                task_id, result_str, error = result
                output.append((tasks[i], result_str, error))
        
        return output
    
    async def shutdown(self) -> None:
        """关闭所有 Worker"""
        # 取消正在运行的任务
        for task_id, task in list(self._running_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._workers.clear()
        logger.info("Worker pool shutdown")


# 全局 Worker 池实例
_worker_pool: WorkerPool | None = None


def get_worker_pool() -> WorkerPool:
    """获取 Worker 池实例"""
    global _worker_pool
    if _worker_pool is None:
        _worker_pool = WorkerPool()
    return _worker_pool


def init_worker_pool(max_workers: int = 5) -> WorkerPool:
    """初始化 Worker 池"""
    global _worker_pool
    _worker_pool = WorkerPool(max_workers)
    return _worker_pool
