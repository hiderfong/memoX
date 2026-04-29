import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.mail_bus import MailBus
from tools.mail import ReadMailTool, SendMailTool


def make_bus():
    return MailBus(task_id="task_test")


def test_send_and_receive():
    bus = make_bus()
    send_tool = SendMailTool("agent_a", bus)
    read_tool = ReadMailTool("agent_b", bus)

    result = asyncio.run(send_tool.execute({
        "to": "agent_b",
        "subject": "你好",
        "body": "正文内容",
    }))
    assert "已发送" in result

    received = asyncio.run(read_tool.execute({}))
    assert "你好" in received
    assert "正文内容" in received


def test_empty_inbox():
    bus = make_bus()
    read_tool = ReadMailTool("agent_a", bus)

    result = asyncio.run(read_tool.execute({}))
    assert "无未读" in result


def test_read_marks_as_read():
    bus = make_bus()
    send_tool = SendMailTool("agent_a", bus)
    read_tool = ReadMailTool("agent_b", bus)

    asyncio.run(send_tool.execute({"to": "agent_b", "subject": "s", "body": "b"}))
    asyncio.run(read_tool.execute({}))   # 第一次读
    result2 = asyncio.run(read_tool.execute({}))  # 第二次读

    assert "无未读" in result2


def test_attachments_in_mail():
    bus = make_bus()
    send_tool = SendMailTool("agent_a", bus)
    read_tool = ReadMailTool("agent_b", bus)

    asyncio.run(send_tool.execute({
        "to": "agent_b",
        "subject": "带附件",
        "body": "见附件",
        "attachments": ["/sandbox/design.md"],
    }))

    received = asyncio.run(read_tool.execute({}))
    assert "/sandbox/design.md" in received
