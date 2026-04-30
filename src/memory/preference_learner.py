"""用户偏好学习器 — 从对话历史中自动学习用户偏好"""

import logging
import re
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.persistence import PersistenceStore

logger = logging.getLogger(__name__)


class PreferenceLearner:
    """用户偏好学习器

    核心功能：
    - 从当前会话中提取用户偏好，存入 memories 表（category=preference）
    - 新会话开始时召回已学习的偏好
    - 将偏好格式化为上下文片段注入提示词
    """

    # 偏好抽取提示词
    _EXTRACT_PROMPT = """你是一个用户偏好分析助手。请从以下对话中提取用户的偏好设置和习惯。

对话：
{conversation}

请用 JSON 格式输出（每条偏好一个对象）：
{{
  "preferences": [
    {{
      "content": "偏好描述（简洁，20-80字）",
      "importance": 1-5（越高越重要）
    }}
  ]
}}

如果没有发现明确偏好，返回空数组：{{"preferences": []}}"""

    def __init__(self, store: "PersistenceStore"):
        self._store = store

    def extract_and_save(
        self,
        messages: list[dict],
        user_id: str | None = None,
        llm_provider=None,
    ) -> int:
        """从对话历史中提取偏好并保存。

        Returns:
            提取到的偏好条数
        """
        if not messages:
            return 0

        # 构建对话文本
        conv_text = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')[:300]}"
            for m in messages
            if m.get("content")
        )

        if llm_provider:
            try:
                import json as _json

                resp = llm_provider.chat(
                    messages=[
                        {"role": "user", "content": self._EXTRACT_PROMPT.format(conversation=conv_text)}
                    ],
                    model=None,
                    temperature=0.2,
                    max_tokens=384,
                )
                text = resp.content or ""
                start, end = text.find("{"), text.rfind("}") + 1
                if start >= 0 and end > start:
                    data = _json.loads(text[start:end])
                    prefs = data.get("preferences", [])
                    for p in prefs:
                        self._store.save_memory(
                            memory_id=str(uuid.uuid4()),
                            content=p["content"][:200],
                            user_id=user_id,
                            category="preference",
                            importance=min(5, max(1, int(p.get("importance", 3)))),
                        )
                    return len(prefs)
            except Exception as e:
                logger.warning(f"[PreferenceLearner] 偏好提取失败: {e}")

        return 0

    def get_preferences(
        self,
        user_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """获取用户已学习的偏好列表（category=preference）"""
        return self._store.list_memories(
            user_id=user_id,
            category="preference",
            limit=limit,
        )

    def format_for_system_prompt(self, preferences: list[dict]) -> str:
        """将偏好列表格式化为系统提示词片段"""
        if not preferences:
            return ""
        lines = ["【用户偏好】"]
        for p in preferences:
            imp = p.get("importance", 3)
            stars = "★" * imp
            lines.append(f"- {p['content']} {stars}")
        return "\n".join(lines)

    def get_and_format(
        self,
        user_id: str | None = None,
        limit: int = 10,
    ) -> str:
        """一行搞定：获取偏好并格式化为系统提示词"""
        prefs = self.get_preferences(user_id=user_id, limit=limit)
        return self.format_for_system_prompt(prefs)

    def clear_preferences(self, user_id: str | None = None) -> int:
        """清除用户偏好记忆，返回删除数量"""
        prefs = self._store.list_memories(user_id=user_id, category="preference", limit=1000)
        count = 0
        for p in prefs:
            if self._store.delete_memory(p["id"]):
                count += 1
        return count
