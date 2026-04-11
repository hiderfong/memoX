"""Worker Agent 池 - 多 Agent 并行执行"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from loguru import logger

from storage import get_store
from .base_agent import (
    AnthropicProvider,
    BaseTool,
    MiniMaxProvider,
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
    icon: str = ""
    display_name: str = ""
    
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

        # Register per-worker LoadSkillTool if this worker has skills configured.
        if config.skills:
            from pathlib import Path
            from config import get_config
            from skills.tool import LoadSkillTool

            skills_dir = Path(get_config().knowledge_base.skills_dir)
            self.tools.register(LoadSkillTool(skills_dir, set(config.skills)))

        # 状态
        self._running = False
        self._current_task_id: str | None = None
        # 改进指令（由 IterativeOrchestrator 在每轮迭代前注入）
        self.refinement_hint: str | None = None

        # Token 用量统计 — 从数据库恢复
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.call_count: int = 0
        self._load_token_usage()

    def _load_token_usage(self) -> None:
        """从数据库加载历史 token 用量"""
        store = get_store()
        if store:
            usage = store.get_worker_token_usage(self.id)
            self.total_input_tokens = usage["input_tokens"]
            self.total_output_tokens = usage["output_tokens"]
            self.call_count = usage["call_count"]

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

        # 检测 Provider 类型，选择对应的工具格式
        is_anthropic_compat = isinstance(self.provider, (AnthropicProvider, MiniMaxProvider))

        # 按 Provider 类型获取工具定义
        if is_anthropic_compat:
            tool_defs = self.tools.get_anthropic_definitions()
        else:
            tool_defs = self.tools.get_definitions()

        for iteration in range(max_iterations):
            # 调用 LLM（传入工具定义）
            chat_kwargs: dict = {
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }
            if tool_defs:
                chat_kwargs["tools"] = tool_defs

            response = await self.provider.chat(
                messages=messages,
                model=self.config.model,
                **chat_kwargs,
            )

            # 累计 token 用量并持久化
            usage = response.usage or {}
            inp = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
            out = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
            self.total_input_tokens += inp
            self.total_output_tokens += out
            self.call_count += 1
            store = get_store()
            if store:
                store.increment_worker_token_usage(self.id, inp, out)

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

                if is_anthropic_compat:
                    # Anthropic 格式：assistant content 包含 tool_use 块
                    messages.append({
                        "role": "assistant",
                        "content": [
                            *([{"type": "text", "text": response.content}] if response.content else []),
                            *[
                                {
                                    "type": "tool_use",
                                    "id": tc.id,
                                    "name": tc.name,
                                    "input": tc.arguments,
                                }
                                for tc in response.tool_calls
                            ],
                        ],
                    })
                    # Anthropic 格式：tool 结果作为 user 消息中的 tool_result 块
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": str(result),
                            }
                            for tc, result in tool_results
                        ],
                    })
                else:
                    # OpenAI 格式：assistant message 带 tool_calls 字段
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
            from pathlib import Path
            from config import get_config
            from skills.loader import list_skills

            skills_dir = Path(get_config().knowledge_base.skills_dir)
            available = list_skills(skills_dir)
            enabled = [s for s in available if s.name in self.config.skills]
            if enabled:
                lines = [f"- **{s.name}**: {s.description}" for s in enabled]
                skills_info = (
                    "\n\n## 可用技能（use the load_skill tool to fetch full content）\n"
                    + "\n".join(lines)
                )
            missing = set(self.config.skills) - {s.name for s in available}
            if missing:
                logger.warning(
                    f"Worker {self.id}: skills not installed: {sorted(missing)}"
                )

        refinement_section = ""
        if self.refinement_hint:
            refinement_section = f"\n\n## 本轮改进要求（来自 Coordinator）\n{self.refinement_hint}"

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
{skills_info}{refinement_section}
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
                "provider": w.config.provider_type,
                "skills": list(w.config.skills),
                "tools": list(w.config.tools),
                "temperature": w.config.temperature,
                "max_tokens": w.config.max_tokens,
                "icon": w.config.icon,
                "display_name": w.config.display_name,
                "token_usage": {
                    "input_tokens": w.total_input_tokens,
                    "output_tokens": w.total_output_tokens,
                    "total_tokens": w.total_input_tokens + w.total_output_tokens,
                    "call_count": w.call_count,
                },
            }
            for w in self._workers.values()
        ]
    
    def get_available_worker(self) -> WorkerAgent | None:
        """获取空闲的 Worker"""
        for worker in self._workers.values():
            if not worker.is_busy:
                return worker
        return None

    def get_worker_for(self, subtask: "SubTask") -> "WorkerAgent | None":
        """获取适合执行该子任务的 Worker

        优先级：
        1. subtask.assigned_agent 精确匹配 Worker name（忽略忙碌状态，等待释放）
        2. 按 Worker skills/tools 能力匹配空闲 Worker
        3. 回退到任意空闲 Worker
        """
        # 1. 按 assigned_agent 精确匹配
        if subtask.assigned_agent and subtask.assigned_agent in self._workers:
            return self._workers[subtask.assigned_agent]

        # 2. 按描述关键词匹配 Worker 名称（例如描述含"测试" → tester worker）
        desc_lower = (subtask.description or "").lower()
        for worker in self._workers.values():
            name_lower = worker.config.name.lower()
            if name_lower in desc_lower or any(
                kw in desc_lower for kw in self._get_worker_keywords(worker)
            ):
                if not worker.is_busy:
                    return worker

        # 3. 回退到任意空闲 Worker
        return self.get_available_worker()

    @staticmethod
    def _get_worker_keywords(worker: "WorkerAgent") -> list[str]:
        """根据 Worker 配置生成匹配关键词"""
        keywords = []
        name = worker.config.name.lower()
        # 基于 Worker 名称的关键词映射
        keyword_map = {
            "code": ["编写", "开发", "代码", "实现", "code", "develop", "implement"],
            "test": ["测试", "验证", "test", "verify", "qa"],
            "research": ["调研", "分析", "搜索", "research", "analyze"],
            "writer": ["撰写", "文档", "报告", "write", "document", "report"],
            "developer": ["编写", "开发", "创建", "develop", "create", "build"],
            "tester": ["测试", "运行测试", "test", "unittest"],
            "processor": ["处理", "加工", "转换", "process", "transform"],
            "reporter": ["报告", "汇总", "生成报告", "report", "summary"],
        }
        for key, kws in keyword_map.items():
            if key in name:
                keywords.extend(kws)
        # 基于 skills 的关键词
        for skill in worker.config.skills:
            keywords.append(skill.lower())
        return keywords

    async def execute_task(
        self,
        task: SubTask,
        context: dict[str, Any] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[str, str | None]:
        """执行单个任务（智能分配 Worker）"""
        worker = self.get_worker_for(task)

        if not worker or worker.is_busy:
            # 优先等待指定 Worker，否则等待任意空闲
            target_id = worker.id if worker else None
            logger.info(f"Waiting for worker {target_id or 'any'} to become available...")
            while True:
                await asyncio.sleep(0.5)
                if target_id and target_id in self._workers and not self._workers[target_id].is_busy:
                    worker = self._workers[target_id]
                    break
                if not target_id:
                    worker = self.get_available_worker()
                    if worker:
                        break

        task.assigned_agent = worker.id
        logger.info(f"Dispatching task {task.id} to worker {worker.id}")
        return await worker.execute_task(task, context, on_progress)
    
    async def execute_parallel(
        self,
        tasks: list[SubTask],
        context: dict[str, Any] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
        per_task_contexts: dict[str, dict] | None = None,
    ) -> list[tuple[SubTask, str | None, str | None]]:
        """并行执行多个任务。per_task_contexts 优先于 context。"""
        async def run_task(task: SubTask) -> tuple[str, str | None, str | None]:
            task_ctx = per_task_contexts.get(task.id, context) if per_task_contexts else context

            progress_callback = None
            if on_progress:
                def callback(msg: str):
                    on_progress(task.id, msg)
                progress_callback = callback

            result, error = await self.execute_task(task, task_ctx, progress_callback)
            return task.id, result, error

        coroutines = [run_task(task) for task in tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=True)

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
