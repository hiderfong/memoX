"""跨会话记忆召回 — 从持久化 memories 表中检索相关记忆"""

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.persistence import PersistenceStore

logger = logging.getLogger(__name__)


class MemoryRecall:
    """跨会话长期记忆管理器

    负责：
    - 从 conversations 中提取有价值的事实/偏好并存储
    - 根据当前会话上下文检索相关记忆
    - 管理记忆的增删改查
    """

    # 自动抽取记忆的提示词（从对话中识别值得记住的事实）
    EXTRACT_PROMPT = """你是一个记忆管理助手。请从以下对话中提取值得长期记住的信息。

对话：
{conversation}

请用 JSON 格式输出（每条记忆一个对象，不要输出其他内容）：
{{
  "memories": [
    {{
      "content": "记忆内容（简洁，20-100字）",
      "category": "fact|preference|context|goal|other",
      "importance": 1-5（越高越重要）
    }}
  ]
}}

如果没有值得记住的信息，返回空的 memories 数组：{{"memories": []}}"""

    def __init__(self, store: "PersistenceStore"):
        self._store = store

    # ─── 记忆写入 ───────────────────────────────────────────────

    def save_from_conversation(
        self,
        messages: list[dict],
        session_id: str,
        user_id: str | None = None,
        llm_provider=None,
    ) -> int:
        """从对话历史中提取记忆并保存。

        Returns:
            提取并保存的记忆条数
        """
        if not messages:
            return 0

        # 构建对话文本
        conv_text = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')[:200]}"
            for m in messages
            if m.get("content")
        )

        if llm_provider:
            try:
                import json as _json

                resp = llm_provider.chat(
                    messages=[{"role": "user", "content": self.EXTRACT_PROMPT.format(conversation=conv_text)}],
                    model=None,
                    temperature=0.3,
                    max_tokens=512,
                )
                text = resp.content or ""
                # 尝试从响应中提取 JSON
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    data = _json.loads(text[start:end])
                    memories = data.get("memories", [])
                    count = 0
                    for m in memories:
                        self._store.save_memory(
                            memory_id=str(uuid.uuid4()),
                            content=m["content"][:500],
                            user_id=user_id,
                            category=m.get("category", "general"),
                            importance=min(5, max(1, int(m.get("importance", 3)))),
                            source_session_id=session_id,
                        )
                        count += 1
                    return count
            except Exception as e:
                logger.warning(f"[MemoryRecall] LLM 记忆提取失败: {e}")

        return 0

    def save_memory(
        self,
        content: str,
        user_id: str | None = None,
        category: str = "general",
        importance: int = 3,
        session_id: str | None = None,
    ) -> str:
        """手动保存一条记忆，返回记忆 ID"""
        memory_id = str(uuid.uuid4())
        self._store.save_memory(
            memory_id=memory_id,
            content=content,
            user_id=user_id,
            category=category,
            importance=importance,
            source_session_id=session_id,
        )
        return memory_id

    # ─── 记忆检索 ───────────────────────────────────────────────

    def recall(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """根据关键词检索相关记忆

        Returns:
            按重要性排序的记忆列表
        """
        if not query or len(query) < 2:
            return []
        return self._store.search_memories(
            query=query,
            user_id=user_id,
            limit=limit,
        )

    def recall_for_session(
        self,
        session_topic: str,
        user_id: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """根据会话主题/首条消息检索相关记忆。

        这是新会话开始时的默认召回调用。
        """
        return self._store.search_memories(
            query=session_topic,
            user_id=user_id,
            limit=limit,
        )

    def get_all(
        self,
        user_id: str | None = None,
        category: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """列出所有记忆（支持过滤）"""
        return self._store.list_memories(
            user_id=user_id,
            category=category,
            limit=limit,
        )

    # ─── 记忆管理 ───────────────────────────────────────────────

    def update_memory(self, memory_id: str, updates: dict) -> bool:
        """更新记忆内容/分类/重要性"""
        return self._store.update_memory(memory_id, updates)

    def delete_memory(self, memory_id: str) -> bool:
        """删除一条记忆"""
        return self._store.delete_memory(memory_id)

    def get_memory(self, memory_id: str) -> dict | None:
        """获取单条记忆详情"""
        return self._store.get_memory(memory_id)

    # ─── 上下文格式化 ───────────────────────────────────────────

    def format_for_context(self, memories: list[dict]) -> str:
        """将记忆列表格式化为可注入上下文的字符串"""
        if not memories:
            return ""
        parts = ["【相关记忆】"]
        for m in memories:
            cat = m.get("category", "general")
            imp = m.get("importance", 3)
            parts.append(f"- [{cat}★{imp}] {m['content']}")
        return "\n".join(parts)
