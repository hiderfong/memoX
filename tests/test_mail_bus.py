import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.mail_bus import MailBus


def test_send_and_read_inbox():
    bus = MailBus(task_id="task_test")

    asyncio.run(bus.send("agent_a", "agent_b", "主题", "正文"))
    messages = asyncio.run(bus.read_inbox("agent_b"))

    assert len(messages) == 1
    assert messages[0].from_agent == "agent_a"
    assert messages[0].subject == "主题"
    assert messages[0].body == "正文"
    assert messages[0].read is True


def test_read_inbox_marks_as_read():
    bus = MailBus(task_id="task_test")

    asyncio.run(bus.send("agent_a", "agent_b", "主题", "正文"))
    asyncio.run(bus.read_inbox("agent_b"))   # 标记已读
    messages2 = asyncio.run(bus.read_inbox("agent_b"))  # 再次读取

    assert len(messages2) == 0  # 已读，不返回


def test_get_all_returns_read_and_unread():
    bus = MailBus(task_id="task_test")

    asyncio.run(bus.send("agent_a", "agent_b", "m1", "body1"))
    asyncio.run(bus.read_inbox("agent_b"))  # 标记已读
    asyncio.run(bus.send("agent_a", "agent_b", "m2", "body2"))

    all_msgs = asyncio.run(bus.get_all("agent_b"))
    assert len(all_msgs) == 2


def test_send_returns_id():
    bus = MailBus(task_id="task_test")
    msg_id = asyncio.run(bus.send("a", "b", "s", "body"))
    assert isinstance(msg_id, str)
    assert len(msg_id) > 0


def test_only_own_inbox():
    bus = MailBus(task_id="task_test")
    asyncio.run(bus.send("agent_a", "agent_b", "msg", "body"))
    msgs_c = asyncio.run(bus.read_inbox("agent_c"))
    assert len(msgs_c) == 0


def test_attachments():
    bus = MailBus(task_id="task_test")
    asyncio.run(bus.send("agent_a", "agent_b", "s", "b", attachments=["/path/file.md"]))
    msgs = asyncio.run(bus.read_inbox("agent_b"))
    assert msgs[0].attachments == ["/path/file.md"]


def test_mark_read():
    bus = MailBus(task_id="task_test")
    asyncio.run(bus.send("agent_a", "agent_b", "s", "b"))
    all_msgs = asyncio.run(bus.get_all("agent_b"))
    msg_id = all_msgs[0].id

    # mark_read by ID
    asyncio.run(bus.mark_read(msg_id))

    # read_inbox should now return 0 (already marked read)
    unread = asyncio.run(bus.read_inbox("agent_b"))
    assert len(unread) == 0

    # mark_read with unknown ID should be silent no-op
    asyncio.run(bus.mark_read("nonexistent_id"))  # should not raise


def test_get_history_returns_all_sorted():
    bus = MailBus(task_id="task_history")
    asyncio.run(bus.send("a", "b", "第一封", "内容1"))
    asyncio.run(bus.send("b", "c", "第二封", "内容2"))
    asyncio.run(bus.send("c", "a", "第三封", "内容3"))

    history = asyncio.run(bus.get_history())
    assert len(history) == 3
    assert history[0].subject == "第一封"
    assert history[1].subject == "第二封"
    assert history[2].subject == "第三封"
    # 按时间排序
    assert history[0].created_at <= history[1].created_at <= history[2].created_at


def test_get_history_empty():
    bus = MailBus(task_id="task_empty")
    history = asyncio.run(bus.get_history())
    assert history == []


def test_export_log_format():
    bus = MailBus(task_id="task_log")
    asyncio.run(bus.send("developer", "tester", "代码就绪", "calculator.py 已创建"))
    asyncio.run(bus.read_inbox("tester"))  # 标记已读
    asyncio.run(bus.send("tester", "developer", "测试通过", "全部 4 个用例通过"))

    log = asyncio.run(bus.export_log())
    assert "邮件通信日志" in log
    assert "task_log" in log
    assert "共 2 封邮件" in log
    assert "developer" in log
    assert "tester" in log
    assert "代码就绪" in log
    assert "测试通过" in log
    assert "已读" in log
    assert "未读" in log


def test_export_log_empty():
    bus = MailBus(task_id="task_nolog")
    log = asyncio.run(bus.export_log())
    assert "(无邮件通信记录)" in log
