"""记忆管理器 - 对话摘要与上下文压缩"""

import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.persistence import PersistenceStore

logger = logging.getLogger(__name__)


@dataclass
class MemoryStats:
    """记忆统计信息"""
    session_id: str
    total_messages: int
    turns: int
    is_compressed: bool
    summary: str


class MemoryManager:
    """会话记忆管理器

    核心功能：
    - 压缩超长对话（超过 max_turns_before_compress 轮）
    - 提供压缩后的上下文（摘要 + 最近未归档消息）
    """

    COMPRESS_PROMPT = """请分析以下对话记录，提取关键信息并生成一段简洁的摘要。

对话记录：
{history}

请用中文输出，格式如下（严格按此格式，不要添加其他内容）：
【摘要】
- 话题：...
- 关键事实：...
- 用户偏好/需求：...
- 未完成事项（如有）：...

【摘要】"""

    def __init__(
        self,
        store: "PersistenceStore",
        max_turns: int = 10,
        summary_max_chars: int = 500,
        recent_messages_to_keep: int = 4,
        llm_provider=None,
    ):
        """初始化记忆管理器

        Args:
            store: 持久化存储
            max_turns: 超过此轮数时触发压缩（默认 10）
            summary_max_chars: 摘要最大字符数
            recent_messages_to_keep: 压缩后保留最近 N 条消息不归档
            llm_provider: LLM provider（用于生成摘要，不提供时跳过 LLM 摘要）
        """
        self._store = store
        self._max_turns = max_turns
        self._summary_max_chars = summary_max_chars
        self._recent = recent_messages_to_keep
        self._llm_provider = llm_provider
        self._lock = threading.Lock()

    def _count_turns(self, messages: list[dict]) -> int:
        """计算对话轮数（以 user 消息数为基准）"""
        return sum(1 for m in messages if m.get("role") == "user")

    def _build_history_text(self, messages: list[dict]) -> str:
        """将消息列表转换为可读的历史文本"""
        lines = []
        for m in messages:
            role = "用户" if m.get("role") == "user" else "助手"
            content = m.get("content", "")
            # 去除媒体标记
            content = re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", content, flags=re.DOTALL)
            content = re.sub(r"!\[.*?\]\(https?://\S+\)", "", content)
            content = content.strip()
            if content:
                lines.append(f"{role}：{content[:300]}")
        return "\n\n".join(lines)

    def _summarize_with_llm(self, history_text: str, llm_provider=None) -> str | None:
        """使用 LLM 生成摘要（失败时返回 None）"""
        provider = llm_provider or self._llm_provider
        if not provider:
            return None
        try:
            response = provider.chat(
                messages=[
                    {"role": "user", "content": self.COMPRESS_PROMPT.format(history=history_text)}
                ],
                model=None,
                temperature=0.3,
                max_tokens=512,
            )
            content = response.content or ""
            # 提取摘要内容
            match = re.search(r"【摘要】\s*(.+?)(?=\n\n【|$)", content, re.DOTALL)
            if match:
                return match.group(1).strip()[:self._summary_max_chars]
            # Fallback: 去除【摘要】标记后返回
            cleaned = re.sub(r"^【摘要】\s*", "", content, count=1).strip()
            return cleaned[:self._summary_max_chars]
        except Exception as e:
            logger.warning(f"[MemoryManager] LLM 摘要生成失败: {e}")
            return None

    def _summarize_fallback(self, messages: list[dict]) -> str:
        """无 LLM 时的规则摘要"""
        user_msgs = [m for m in messages if m.get("role") == "user"]
        topics = [m["content"][:50] for m in user_msgs[-3:]]
        return f"对话约 {len(user_msgs)} 轮，主要涉及：{'; '.join(topics)}"

    def compress_session(self, session_id: str) -> MemoryStats | None:
        """压缩指定会话

        Returns:
            MemoryStats 压缩后的统计信息，未触发压缩时返回 None
        """
        with self._lock:
            messages = self._store.get_session_messages(session_id)
            turns = self._count_turns(messages)

            # 检查是否需要压缩
            if turns <= self._max_turns:
                return None

            existing_summary = self._store.get_session_summary(session_id)
            if existing_summary:
                # 已压缩过，跳过
                return None

            # 构建历史文本用于摘要
            history_text = self._build_history_text(messages[:-self._recent * 2]) if len(messages) > self._recent * 2 else self._build_history_text(messages)

            # 生成摘要
            summary = self._summarize_with_llm(history_text)
            if not summary:
                summary = self._summarize_fallback(messages)

            # 保存摘要
            self._store.save_session_summary(session_id, summary)

            # 归档早期消息（保留最近 _recent*2 条，足够保留最近一对完整对话）
            msgs_to_archive = len(messages) - self._recent
            if msgs_to_archive > 0 and messages:
                # 找到要归档的最后一条消息的 ID
                # chat_messages 表有自增 id，我们取前 N 条归档
                # 由于是按 id 顺序，我们取第 msgs_to_archive 条
                # 注意：消息已经是按 id 排序的了
                try:
                    rows = self._store._conn.execute(
                        "SELECT id FROM chat_messages WHERE session_id=? ORDER BY id LIMIT 1 OFFSET ?",
                        (session_id, msgs_to_archive - 1),
                    ).fetchone()
                    if rows:
                        cutoff_id = rows["id"]
                        self._store.archive_messages(session_id, cutoff_id)
                except Exception as e:
                    logger.warning(f"[MemoryManager] 归档消息失败: {e}")

            logger.info(f"[MemoryManager] 会话 {session_id} 压缩完成: {turns} 轮 → 摘要 {len(summary)} 字符")

            return MemoryStats(
                session_id=session_id,
                total_messages=len(messages),
                turns=turns,
                is_compressed=True,
                summary=summary,
            )

    def compress_if_needed(
        self, session_id: str, llm_provider=None, force: bool = False
    ) -> tuple[str, int]:
        """检查是否需要压缩，必要时执行压缩。

        Returns:
            (summary_or_empty, archived_count) 元组
        """
        if force:
            # 强制压缩路径：先生成摘要再压缩
            stats = self._compress_with_provider(session_id, llm_provider)
            if stats:
                msgs = self._store.get_session_messages(session_id)
                archived = len(msgs) - self._recent if msgs else 0
                return stats.summary, max(0, archived)
            return "", 0

        # 智能检查路径
        messages = self._store.get_session_messages(session_id)
        turns = self._count_turns(messages)
        existing = self._store.get_session_summary(session_id)

        if turns <= self._max_turns and not force:
            return existing or "", 0

        if existing and not force:
            return existing, 0

        # 需要压缩
        provider = llm_provider or self._llm_provider
        if not provider:
            return "", 0

        stats = self._compress_with_provider(session_id, provider)
        if stats:
            msgs = self._store.get_session_messages(session_id)
            archived = len(msgs) - self._recent if msgs else 0
            return stats.summary, max(0, archived)
        return "", 0

    def _compress_with_provider(self, session_id: str, llm_provider) -> MemoryStats | None:
        """使用指定 provider 执行压缩（内部方法）"""
        with self._lock:
            messages = self._store.get_session_messages(session_id)
            if not messages:
                return None

            history_text = (
                self._build_history_text(messages[:- self._recent * 2])
                if len(messages) > self._recent * 2
                else self._build_history_text(messages)
            )

            summary = self._summarize_with_llm(history_text, llm_provider)
            if not summary:
                summary = self._summarize_fallback(messages)

            self._store.save_session_summary(session_id, summary)

            msgs_to_archive = len(messages) - self._recent
            if msgs_to_archive > 0:
                try:
                    rows = self._store._conn.execute(
                        "SELECT id FROM chat_messages WHERE session_id=? ORDER BY id LIMIT 1 OFFSET ?",
                        (session_id, msgs_to_archive - 1),
                    ).fetchone()
                    if rows:
                        self._store.archive_messages(session_id, rows["id"])
                except Exception as e:
                    logger.warning(f"[MemoryManager] 归档消息失败: {e}")

            logger.info(f"[MemoryManager] 会话 {session_id} 压缩完成")

            return MemoryStats(
                session_id=session_id,
                total_messages=len(messages),
                turns=self._count_turns(messages),
                is_compressed=True,
                summary=summary,
            )

    def get_context(self, session_id: str) -> tuple[str, list[dict]]:
        """获取压缩后的上下文

        Returns:
            (summary, recent_messages) 元组
            - summary: 会话摘要（如果已压缩）
            - recent_messages: 未归档的最近消息列表
        """
        summary = self._store.get_session_summary(session_id)
        # 只获取未归档消息
        messages = self._store.get_session_messages(session_id, include_archived=False)
        return summary, messages

    def get_stats(self, session_id: str) -> MemoryStats:
        """获取会话记忆统计"""
        messages = self._store.get_session_messages(session_id, include_archived=False)
        turns = self._count_turns(messages)
        summary = self._store.get_session_summary(session_id)
        return MemoryStats(
            session_id=session_id,
            total_messages=len(messages),
            turns=turns,
            is_compressed=bool(summary),
            summary=summary,
        )
