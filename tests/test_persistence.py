"""SQLite 持久化存储测试"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from storage.persistence import PersistenceStore


def test_session_create_and_list(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session("s1", "测试会话")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"
    assert sessions[0]["title"] == "测试会话"
    store.close()


def test_message_save_and_retrieve(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("s1", "user", "你好")
    store.save_message("s1", "assistant", "你好！有什么可以帮你的？")

    messages = store.get_session_messages("s1")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"
    assert messages[1]["role"] == "assistant"
    store.close()


def test_message_auto_creates_session(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("auto_s", "user", "消息")
    sessions = store.list_sessions()
    assert any(s["id"] == "auto_s" for s in sessions)
    store.close()


def test_delete_session(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_message("s1", "user", "msg")
    assert store.delete_session("s1") is True
    assert store.get_session_messages("s1") == []
    assert store.delete_session("nonexistent") is False
    store.close()


def test_task_save_and_retrieve(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    task_data = {
        "task_id": "task_001",
        "description": "编写计算器",
        "result": "已完成",
        "final_score": 0.85,
        "iterations": [{"iteration": 0, "score": 0.85, "improvements": []}],
        "mail_log": "=== 邮件日志 ===",
        "shared_dir": "/tmp/shared",
        "suggestions": [{"type": "code_quality", "title": "改进"}],
    }
    store.save_task(task_data)

    retrieved = store.get_task("task_001")
    assert retrieved is not None
    assert retrieved["task_id"] == "task_001"
    assert retrieved["description"] == "编写计算器"
    assert retrieved["final_score"] == 0.85
    assert len(retrieved["iterations"]) == 1
    assert retrieved["mail_log"] == "=== 邮件日志 ==="
    store.close()


def test_task_list_ordered_by_time(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    for i in range(3):
        store.save_task({"task_id": f"task_{i}", "description": f"任务{i}", "result": "", "final_score": 0.5})

    tasks = store.list_tasks()
    assert len(tasks) == 3
    # 最近的排在前面
    assert tasks[0]["task_id"] == "task_2"
    store.close()


def test_task_upsert(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_task({"task_id": "t1", "description": "v1", "result": "", "final_score": 0.3})
    store.save_task({"task_id": "t1", "description": "v1", "result": "更新", "final_score": 0.9})

    task = store.get_task("t1")
    assert task["final_score"] == 0.9
    assert task["result"] == "更新"
    # 应该只有 1 条记录
    assert len(store.list_tasks()) == 1
    store.close()


def test_get_nonexistent_task(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    assert store.get_task("nonexistent") is None
    store.close()


def test_update_session_title(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session("s1")
    store.update_session_title("s1", "新标题")
    sessions = store.list_sessions()
    assert sessions[0]["title"] == "新标题"
    store.close()
