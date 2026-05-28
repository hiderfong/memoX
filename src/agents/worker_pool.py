"""Worker Agent 池 - 多 Agent 并行执行"""

import asyncio
import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
from loguru import logger

from storage import get_store

from .base_agent import (
    AnthropicProvider,
    LLMProvider,
    MiniMaxProvider,
    ToolRegistry,
    create_provider,
    get_provider_capabilities,
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
    acceptance_criteria: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    assigned_agent: str | None = None
    attempts: int = 0
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
class ProviderFallbackConfig:
    """Fallback provider route for a worker."""

    provider_type: str
    api_key: str
    model: str
    base_url: str = ""
    headers: dict[str, Any] = field(default_factory=dict)


@dataclass
class _ProviderRoute:
    provider_type: str
    model: str
    provider: LLMProvider
    is_fallback: bool = False


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
    fallback_providers: list[ProviderFallbackConfig] = field(default_factory=list)
    provider_retry_attempts: int = 1
    provider_retry_backoff_seconds: float = 0.5
    icon: str = ""
    display_name: str = ""

    @classmethod
    def from_template(cls, name: str, template: dict, api_keys: dict[str, str]) -> "WorkerConfig":
        """从模板创建配置"""
        provider = template.get("provider", "anthropic")
        api_key = api_keys.get(provider, "")
        fallback_templates = template.get("fallback_providers") or []
        if not isinstance(fallback_templates, list):
            fallback_templates = []

        return cls(
            name=name,
            provider_type=provider,
            api_key=api_key,
            model=template.get("model", "claude-sonnet-4-20250514"),
            temperature=template.get("temperature", 0.7),
            max_tokens=template.get("max_tokens", 4096),
            tools=template.get("tools", []),
            skills=template.get("skills", []),
            fallback_providers=[
                ProviderFallbackConfig(
                    provider_type=str(item.get("provider", "")),
                    api_key=api_keys.get(str(item.get("provider", "")), ""),
                    model=str(item.get("model") or template.get("model", "claude-sonnet-4-20250514")),
                    base_url=str(item.get("base_url", "")),
                    headers=dict(item.get("headers", {}) or {}),
                )
                for item in fallback_templates
                if isinstance(item, dict) and item.get("provider")
            ],
        )


def _provider_protocol(provider_type: str) -> str:
    capabilities = get_provider_capabilities(provider_type)
    return capabilities.protocol if capabilities else provider_type.lower()


def _messages_include_tool_roundtrip(messages: list[dict]) -> bool:
    for message in messages:
        if message.get("role") == "tool":
            return True
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"}:
                    return True
    return False


def _is_retryable_provider_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code in {408, 409, 425, 429} or status_code >= 500
    return False


def _provider_error_summary(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {str(exc)[:160]}"


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
        self._provider_routes: list[_ProviderRoute] = []
        self.refresh_provider_routes(self.provider)

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

        # 最近日志（环形缓冲区，保留最后 MAX_LOGS 条）
        from collections import deque
        self._log_buffer: deque[dict] = deque(maxlen=100)
        self._log_lock = __import__("threading").Lock()

    def refresh_provider_routes(self, provider: LLMProvider | None = None) -> None:
        """Rebuild primary and fallback provider routes after config changes."""
        if provider is not None:
            self.provider = provider
        self._provider_routes = [
            _ProviderRoute(
                provider_type=self.config.provider_type,
                model=self.config.model,
                provider=self.provider,
                is_fallback=False,
            )
        ]
        self._provider_routes.extend(self._build_fallback_routes(self.config.fallback_providers))

    def _build_fallback_routes(self, fallbacks: list[ProviderFallbackConfig]) -> list[_ProviderRoute]:
        routes: list[_ProviderRoute] = []
        for fallback in fallbacks:
            if not fallback.provider_type or not fallback.api_key or not fallback.model:
                continue
            try:
                provider = create_provider(
                    fallback.provider_type,
                    fallback.api_key,
                    base_url=fallback.base_url,
                    headers=fallback.headers,
                )
            except Exception as exc:
                logger.warning(
                    f"Worker {self.id}: fallback provider {fallback.provider_type!r} 初始化失败: {exc}"
                )
                continue
            routes.append(
                _ProviderRoute(
                    provider_type=fallback.provider_type,
                    model=fallback.model,
                    provider=provider,
                    is_fallback=True,
                )
            )
        return routes

    def add_log(self, level: str, message: str, meta: dict | None = None) -> None:
        """追加一条日志（线程安全）"""
        import datetime as dt
        entry = {
            "timestamp": dt.datetime.now().isoformat(),
            "level": level,
            "message": message,
            "meta": meta or {},
        }
        with self._log_lock:
            self._log_buffer.append(entry)

    def get_logs(self, limit: int = 50) -> list[dict]:
        """获取最近 limit 条日志"""
        with self._log_lock:
            return list(self._log_buffer)[-limit:]

    def clear_logs(self) -> None:
        """清空日志缓冲区"""
        with self._log_lock:
            self._log_buffer.clear()

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
        self.tools.set_audit_context({
            "worker_id": self.id,
            "worker_name": self.config.name,
            "task_id": task.id,
            "subtask_id": task.id,
        })
        self.add_log("info", f"任务开始: {task.description[:60]}...", {"task_id": task.id})

        logger.info(f"Worker {self.id} starting task {task.id}: {task.description[:50]}...")

        try:
            # 构建系统提示
            system_prompt = self._build_system_prompt()

            # 构建用户消息
            user_content = task.description
            criteria_section = ""
            if task.acceptance_criteria:
                criteria = "\n".join(f"- {item}" for item in task.acceptance_criteria)
                criteria_section = f"\n\n验收标准：\n{criteria}"
                user_content = f"{user_content}{criteria_section}"
            if context:
                context_str = json.dumps(context, ensure_ascii=False, indent=2)
                user_content = f"""上下文信息：
```json
{context_str}
```

任务：
{task.description}{criteria_section}"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            # 执行 Agent 循环
            result = await self._run_agent_loop(messages, on_progress)

            self._running = False
            self._current_task_id = None
            self.tools.set_audit_context({})
            self.add_log("info", "任务完成", {"task_id": task.id, "result_len": len(result)})

            logger.info(f"Worker {self.id} completed task {task.id}")
            return result, None

        except Exception as e:
            error_msg = f"Error: {type(e).__name__}: {str(e)}"
            self.add_log("error", error_msg, {"task_id": task.id})
            logger.error(f"Worker {self.id} failed task {task.id}: {e}")

            self._running = False
            self._current_task_id = None
            self.tools.set_audit_context({})

            return "", error_msg

    async def _run_agent_loop(
        self,
        messages: list[dict],
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Agent 执行循环"""
        max_iterations = self.config.max_iterations

        for _iteration in range(max_iterations):
            # 调用 LLM（传入工具定义）
            chat_kwargs: dict = {
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }

            response, active_provider = await self._chat_with_provider_fallback(
                messages=messages,
                base_kwargs=chat_kwargs,
                on_progress=on_progress,
            )
            is_anthropic_compat = isinstance(active_provider, (AnthropicProvider, MiniMaxProvider))

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
            self.add_log("info", "LLM 调用完成", {"input_tokens": inp, "output_tokens": out, "call_count": self.call_count})

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
                    assistant_message = {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [tc.to_dict() for tc in response.tool_calls],
                    }
                    if (
                        response.reasoning_content
                        and getattr(active_provider, "preserve_reasoning_content", False)
                    ):
                        assistant_message["reasoning_content"] = response.reasoning_content
                    messages.append(assistant_message)
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

    async def _chat_with_provider_fallback(
        self,
        messages: list[dict],
        base_kwargs: dict[str, Any],
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[Any, LLMProvider]:
        """Call the active provider, retrying transient failures and falling back when configured."""
        last_error: Exception | None = None
        primary_protocol = _provider_protocol(self._provider_routes[0].provider_type)
        has_tool_roundtrip = _messages_include_tool_roundtrip(messages)

        for route_index, route in enumerate(self._provider_routes):
            route_protocol = _provider_protocol(route.provider_type)
            if has_tool_roundtrip and route_protocol != primary_protocol:
                self.add_log(
                    "warning",
                    "跳过跨协议 fallback provider",
                    {
                        "provider": route.provider_type,
                        "reason": "message_history_contains_tool_results",
                    },
                )
                continue

            attempts = max(1, int(self.config.provider_retry_attempts) + 1)
            for attempt in range(attempts):
                chat_kwargs = dict(base_kwargs)
                tool_defs = self._tool_definitions_for_provider(route.provider)
                if tool_defs:
                    chat_kwargs["tools"] = tool_defs
                try:
                    response = await route.provider.chat(
                        messages=messages,
                        model=route.model,
                        **chat_kwargs,
                    )
                    if route.is_fallback or attempt > 0:
                        self.add_log(
                            "info",
                            "LLM provider 调用恢复",
                            {
                                "provider": route.provider_type,
                                "model": route.model,
                                "attempt": attempt + 1,
                                "fallback": route.is_fallback,
                            },
                        )
                    return response, route.provider
                except Exception as exc:
                    last_error = exc
                    if not _is_retryable_provider_error(exc):
                        raise
                    details = {
                        "provider": route.provider_type,
                        "model": route.model,
                        "attempt": attempt + 1,
                        "max_attempts": attempts,
                        "fallback": route.is_fallback,
                        "error": _provider_error_summary(exc),
                    }
                    self.add_log("warning", "LLM provider transient failure", details)
                    logger.warning(f"Worker {self.id}: provider transient failure: {details}")
                    if on_progress:
                        if attempt + 1 < attempts:
                            on_progress(
                                f"provider_retry: {route.provider_type}/{route.model} "
                                f"attempt {attempt + 1} failed: {details['error']}"
                            )
                        else:
                            next_route = next(
                                (
                                    candidate
                                    for candidate in self._provider_routes[route_index + 1:]
                                    if not (
                                        has_tool_roundtrip
                                        and _provider_protocol(candidate.provider_type) != primary_protocol
                                    )
                                ),
                                None,
                            )
                            if next_route:
                                on_progress(
                                    f"provider_fallback: {route.provider_type}/{route.model} -> "
                                    f"{next_route.provider_type}/{next_route.model}: {details['error']}"
                                )
                    if attempt + 1 < attempts:
                        await asyncio.sleep(max(0.0, float(self.config.provider_retry_backoff_seconds)))
                        continue
                    break

        if last_error:
            raise last_error
        raise RuntimeError("No provider route available for worker")

    def _tool_definitions_for_provider(self, provider: LLMProvider) -> list[dict]:
        if isinstance(provider, (AnthropicProvider, MiniMaxProvider)):
            return self.tools.get_anthropic_definitions()
        return self.tools.get_definitions()

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
3. 严格遵守任务正文和验收标准中的文件名、字段名、数据格式、收件人、主题和固定内容
4. 如果任务要求写入特定内容，不要用占位符、空对象或自行改写的数据替代
5. 写入关键文件后，优先用 read_file 复核内容是否满足验收标准
6. 如果遇到问题，尝试替代方案
7. 完成后返回简洁的结果摘要
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
                "fallback_providers": [
                    {
                        "provider": fallback.provider_type,
                        "model": fallback.model,
                        "base_url": fallback.base_url,
                        "headers": {},
                    }
                    for fallback in w.config.fallback_providers
                ],
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
                "recent_logs": w.get_logs(10),
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

        # 2. 按描述关键词匹配打分：Worker 名称命中 +2，每个关键词命中 +1
        desc_lower = (subtask.description or "").lower()
        best_worker: WorkerAgent | None = None
        best_score = 0
        for worker in self._workers.values():
            if worker.is_busy:
                continue
            name_lower = worker.config.name.lower()
            score = 2 if name_lower in desc_lower else 0
            for kw in self._get_worker_keywords(worker):
                if kw in desc_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best_worker = worker
        if best_worker is not None:
            return best_worker

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
        for _task_id, task in list(self._running_tasks.items()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
