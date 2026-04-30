"""MemoryManager 单元测试"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.persistence import PersistenceStore
from memory.memory_manager import MemoryManager, MemoryStats


# ─── Mock LLM Provider ────────────────────────────────────────────────────────


class MockLLMProvider:
    """可配置返回内容的 Mock LLM Provider"""

    def __init__(self, response_content: str = ""):
        self.calls: list[dict] = []
        self._response = response_content

    def chat(self, messages, model=None, temperature=None, max_tokens=None):
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        response = MagicMock()
        response.content = self._response
        return response


# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _make_store(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    # 创建一些测试消息
    store.save_message("s1", "user", "我想学习 Python")
    store.save_message("s1", "assistant", "Python 是一门很好的编程语言。")
    store.save_message("s1", "user", "它适合做什么？")
    store.save_message("s1", "assistant", "Web开发、数据科学、AI都能做。")
    return store


# ─── 核心逻辑测试 ──────────────────────────────────────────────────────────────


def test_count_turns():
    """_count_turns 只统计 user 消息"""
    store = PersistenceStore(":memory:")
    manager = MemoryManager(store, max_turns=10)

    # 0 轮
    assert manager._count_turns([]) == 0

    # 1 轮 user
    assert manager._count_turns([{"role": "user", "content": "hi"}]) == 1

    # 多轮对话
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "bye"},
        {"role": "assistant", "content": "bye"},
    ]
    assert manager._count_turns(msgs) == 2


def test_build_history_text_strips_media(tmp_path):
    """_build_history_text 正确去除 [[IMAGE:...]] 等媒体标记"""
    store = PersistenceStore(tmp_path / "test.db")
    manager = MemoryManager(store)

    msgs = [
        {"role": "user", "content": "生成一张图 [[IMAGE: a cat]]"},
        {"role": "assistant", "content": "![cat](https://example.com/cat.png) 好的！"},
    ]
    text = manager._build_history_text(msgs)
    assert "[[IMAGE:" not in text
    assert "![cat]" not in text
    assert "生成一张图" in text
    assert "好的！" in text


def test_build_history_text_truncates_long_content(tmp_path):
    """长消息内容被截断到 300 字符"""
    store = PersistenceStore(tmp_path / "test.db")
    manager = MemoryManager(store)

    long_content = "x" * 500
    msgs = [{"role": "user", "content": long_content}]
    text = manager._build_history_text(msgs)
    assert len(text) < 500


# ─── compress_if_needed 行为测试 ──────────────────────────────────────────────


def test_compress_skipped_under_threshold(tmp_path):
    """轮数未超过阈值时不触发压缩"""
    store = PersistenceStore(tmp_path / "test.db")
    # 只有 2 轮，阈值 10，不应触发
    store.save_message("s1", "user", "hi")
    store.save_message("s1", "assistant", "hi")
    store.save_message("s1", "user", "bye")
    store.save_message("s1", "assistant", "bye")

    manager = MemoryManager(store, max_turns=10, llm_provider=MockLLMProvider())
    summary, archived = manager.compress_if_needed("s1", None)

    assert summary == ""
    assert archived == 0
    # 消息未被归档
    msgs = store.get_session_messages("s1")
    assert len(msgs) == 4


def test_compress_skipped_when_already_compressed(tmp_path):
    """已压缩的会话不重复压缩"""
    store = PersistenceStore(tmp_path / "test.db")
    # 提前写入摘要
    store.save_session_summary("s1", "已有摘要")

    # 再加 20 轮触发压缩
    for i in range(20):
        store.save_message("s1", "user", f"msg {i}")

    manager = MemoryManager(store, max_turns=5, llm_provider=MockLLMProvider())
    summary, archived = manager.compress_if_needed("s1", None)

    assert summary == "已有摘要"
    assert archived == 0


def test_compress_triggers_over_threshold_with_llm(tmp_path):
    """超过阈值时触发 LLM 压缩"""
    store = PersistenceStore(tmp_path / "test.db")
    mock = MockLLMProvider("【摘要】\n- 话题：Python学习\n- 关键事实：用户想学Python\n- 用户偏好：无\n- 未完成事项：无\n\n【摘要】")

    # 超过阈值的多轮对话
    for i in range(12):
        store.save_message("s1", "user", f"问题{i}")
        store.save_message("s1", "assistant", f"回答{i}")

    manager = MemoryManager(store, max_turns=10, llm_provider=mock)
    summary, archived = manager.compress_if_needed("s1", mock)

    assert "Python学习" in summary
    assert archived > 0
    # 验证 LLM 被调用了
    assert len(mock.calls) >= 1
    assert "对话记录" in mock.calls[-1]["messages"][0]["content"]


def test_compress_fallback_when_no_provider(tmp_path):
    """无 LLM provider 时使用规则摘要降级"""
    store = PersistenceStore(tmp_path / "test.db")

    for i in range(12):
        store.save_message("s1", "user", f"Python问题{i}")
        store.save_message("s1", "assistant", f"回答{i}")

    # 不传 llm_provider，依赖 fallback 逻辑
    manager = MemoryManager(store, max_turns=10, llm_provider=None)
    # 手动触发 compress_with_provider，传入 None provider 以测试 fallback
    stats = manager._compress_with_provider("s1", None)

    assert stats is not None
    assert stats.summary != ""
    assert "Python" in stats.summary  # fallback 包含话题关键词


def test_compress_force_regenerates(tmp_path):
    """force=True 强制重新压缩"""
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session_summary("s1", "旧摘要")

    mock = MockLLMProvider("【摘要】\n- 话题：新话题\n- 关键事实：新事实\n- 用户偏好：无\n- 未完成事项：无\n\n【摘要】")

    for i in range(12):
        store.save_message("s1", "user", f"msg{i}")

    manager = MemoryManager(store, max_turns=10, llm_provider=mock)
    summary, archived = manager.compress_if_needed("s1", mock, force=True)

    assert "新话题" in summary


def test_compress_archives_correct_messages(tmp_path):
    """压缩后早期消息被归档，最近 N 条保留"""
    store = PersistenceStore(tmp_path / "test.db")

    # 写入 15 条消息（15 > max_turns=5 + recent=4）
    for i in range(15):
        store.save_message("s1", "user", f"user{i}")
        store.save_message("s1", "assistant", f"assistant{i}")

    manager = MemoryManager(store, max_turns=5, recent_messages_to_keep=4, llm_provider=None)
    summary, archived = manager.compress_if_needed("s1", None, force=True)

    # 归档了 15 - 4 = 11 条
    assert archived >= 11

    # 未归档消息只剩最近 4 条
    unarchived = store.get_session_messages("s1", include_archived=False)
    assert len(unarchived) == 4
    assert unarchived[-1]["content"] == "assistant14"


# ─── get_context 测试 ─────────────────────────────────────────────────────────


def test_get_context_returns_summary_and_unarchived(tmp_path):
    """get_context 返回 (摘要, 未归档消息)"""
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session_summary("s1", "测试摘要")

    store.save_message("s1", "user", "旧消息（已归档）")
    store.save_message("s1", "assistant", "旧回答")
    store.save_message("s1", "user", "旧消息2（已归档）")
    store.save_message("s1", "assistant", "旧回答2（已归档）")
    # 归档全部4条旧消息（取第4条的id作为截止点）
    rows = store._conn.execute(
        "SELECT id FROM chat_messages WHERE session_id='s1' ORDER BY id LIMIT 1 OFFSET 3"
    ).fetchone()
    if rows:
        store.archive_messages("s1", rows["id"])

    store.save_message("s1", "user", "新消息")
    store.save_message("s1", "assistant", "新回答")

    manager = MemoryManager(store, max_turns=10)
    summary, msgs = manager.get_context("s1")

    assert summary == "测试摘要"
    assert len(msgs) == 2  # 只有新消息
    assert msgs[0]["content"] == "新消息"


def test_get_context_empty_session(tmp_path):
    """空会话返回空摘要和空消息列表"""
    store = PersistenceStore(tmp_path / "test.db")
    manager = MemoryManager(store, max_turns=10)
    summary, msgs = manager.get_context("nonexistent")
    assert summary == ""
    assert msgs == []


# ─── get_stats 测试 ───────────────────────────────────────────────────────────


def test_get_stats(tmp_path):
    """get_stats 返回正确的统计信息"""
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("s1", "user", "q1")
    store.save_message("s1", "assistant", "a1")
    store.save_message("s1", "user", "q2")
    store.save_message("s1", "assistant", "a2")

    manager = MemoryManager(store, max_turns=10)
    stats = manager.get_stats("s1")

    assert isinstance(stats, MemoryStats)
    assert stats.session_id == "s1"
    assert stats.turns == 2
    assert stats.is_compressed is False
    assert stats.summary == ""


def test_get_stats_after_compression(tmp_path):
    """压缩后 is_compressed=True"""
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session_summary("s1", "压缩后摘要")

    manager = MemoryManager(store, max_turns=10)
    stats = manager.get_stats("s1")

    assert stats.is_compressed is True
    assert stats.summary == "压缩后摘要"


# ─── clear_archived_messages 测试 ────────────────────────────────────────────


def test_clear_archived_messages(tmp_path):
    """clear_archived_messages 清除所有归档标记"""
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("s1", "user", "msg1")
    store.save_message("s1", "assistant", "ans1")

    # 归档消息
    rows = store._conn.execute(
        "SELECT id FROM chat_messages WHERE session_id='s1' ORDER BY id LIMIT 1"
    ).fetchone()
    if rows:
        store.archive_messages("s1", rows["id"])

    # 验证归档成功
    archived = store.get_session_messages("s1", include_archived=True)
    assert any(m.get("metadata", {}).get("archived") for m in archived)

    # 清除归档
    store.clear_archived_messages("s1")

    # 再次查询，所有消息都应该是未归档
    all_msgs = store.get_session_messages("s1", include_archived=True)
    assert not any(m.get("metadata", {}).get("archived") for m in all_msgs)


# ─── save_session_summary 测试 ────────────────────────────────────────────────


def test_save_and_get_session_summary(tmp_path):
    """摘要正确保存和读取"""
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("s1", "user", "hi")

    assert store.get_session_summary("s1") == ""

    store.save_session_summary("s1", "测试摘要内容")
    assert store.get_session_summary("s1") == "测试摘要内容"

    # 覆盖
    store.save_session_summary("s1", "新摘要")
    assert store.get_session_summary("s1") == "新摘要"
