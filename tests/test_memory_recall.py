"""MemoryRecall 单元测试"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage.persistence import PersistenceStore
from memory.memory_recall import MemoryRecall


def _make_recall(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    return MemoryRecall(store)


def test_save_and_get_memory(tmp_path):
    """手动保存和获取记忆"""
    recall = _make_recall(tmp_path)
    memory_id = recall.save_memory(
        content="用户喜欢在下午工作",
        category="preference",
        importance=4,
    )
    assert memory_id

    mem = recall.get_memory(memory_id)
    assert mem is not None
    assert mem["content"] == "用户喜欢在下午工作"
    assert mem["category"] == "preference"
    assert mem["importance"] == 4
    assert mem["user_id"] is None


def test_save_memory_with_user_id(tmp_path):
    """带 user_id 保存记忆"""
    recall = _make_recall(tmp_path)
    memory_id = recall.save_memory(
        content="用户是数据科学家",
        user_id="alice",
        category="fact",
    )
    mem = recall.get_memory(memory_id)
    assert mem["user_id"] == "alice"


def test_update_memory(tmp_path):
    """更新记忆内容"""
    recall = _make_recall(tmp_path)
    memory_id = recall.save_memory(content="原始内容", importance=3)

    success = recall.update_memory(memory_id, {"content": "更新后内容", "importance": 5})
    assert success

    mem = recall.get_memory(memory_id)
    assert mem["content"] == "更新后内容"
    assert mem["importance"] == 5


def test_delete_memory(tmp_path):
    """删除记忆"""
    recall = _make_recall(tmp_path)
    memory_id = recall.save_memory(content="待删除")

    assert recall.delete_memory(memory_id) is True
    assert recall.get_memory(memory_id) is None
    assert recall.delete_memory(memory_id) is False  # 重复删除


def test_recall_search(tmp_path):
    """关键词搜索记忆"""
    recall = _make_recall(tmp_path)
    recall.save_memory(content="用户使用 Python 编程", category="fact", importance=5)
    recall.save_memory(content="用户喜欢咖啡", category="preference", importance=3)
    recall.save_memory(content="Python 是主流语言", category="fact", importance=4)

    results = recall.recall("Python")
    assert len(results) >= 2
    assert all("Python" in r["content"] for r in results)


def test_recall_empty_query(tmp_path):
    """空查询返回空"""
    recall = _make_recall(tmp_path)
    recall.save_memory(content="测试")
    results = recall.recall("")
    assert results == []
    results = recall.recall("x")  # 1 char also returns empty
    assert results == []


def test_recall_filters_by_user(tmp_path):
    """搜索可按用户过滤"""
    recall = _make_recall(tmp_path)
    recall.save_memory(content="Alice 的记忆", user_id="alice")
    recall.save_memory(content="Bob 的记忆", user_id="bob")

    alice_results = recall.recall("记忆", user_id="alice")
    assert len(alice_results) == 1
    assert alice_results[0]["user_id"] == "alice"


def test_get_all(tmp_path):
    """列出所有记忆"""
    recall = _make_recall(tmp_path)
    recall.save_memory(content="记忆1", category="fact", importance=3)
    recall.save_memory(content="记忆2", category="preference", importance=5)
    recall.save_memory(content="记忆3", category="fact", importance=2)

    all_memories = recall.get_all()
    assert len(all_memories) == 3

    facts = recall.get_all(category="fact")
    assert len(facts) == 2


def test_format_for_context(tmp_path):
    """格式化为上下文字符串"""
    recall = _make_recall(tmp_path)
    memory_id = recall.save_memory(
        content="用户喜欢 Python",
        category="preference",
        importance=4,
    )
    mem = recall.get_memory(memory_id)

    formatted = recall.format_for_context([mem])
    assert "【相关记忆】" in formatted
    assert "Python" in formatted
    assert "preference" in formatted
    assert "★" in formatted


def test_format_for_context_empty(tmp_path):
    """空列表返回空字符串"""
    recall = _make_recall(tmp_path)
    assert recall.format_for_context([]) == ""
