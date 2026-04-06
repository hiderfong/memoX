# tests/test_integration_multiagent.py
import sys, os, asyncio, json, pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.mail_bus import MailBus
from agents.sandbox import SandboxManager
from tools.mail import SendMailTool, ReadMailTool


def test_mailbus_communication(tmp_path):
    """Worker A 通过 SendMailTool 发消息，Worker B 通过 ReadMailTool 收到"""
    sandbox_mgr = SandboxManager(tmp_path)
    task_id = "task_comm"
    sandbox_mgr.create_task_workspace(task_id)
    mail_bus = MailBus(task_id=task_id)

    send_tool = SendMailTool("worker_a", mail_bus)
    read_tool = ReadMailTool("worker_b", mail_bus)

    # Worker A 发送
    send_result = asyncio.run(send_tool.execute({
        "to": "worker_b",
        "subject": "协作通知",
        "body": "文件已就绪，请处理",
    }))
    assert "已发送" in send_result

    # Worker B 读取
    read_result = asyncio.run(read_tool.execute({}))
    assert "协作通知" in read_result
    assert "文件已就绪，请处理" in read_result

    # 再次读取：已读消息不再返回
    read_result2 = asyncio.run(read_tool.execute({}))
    assert "无未读邮件" in read_result2

    # MailBus 层面验证
    all_msgs = asyncio.run(mail_bus.get_all("worker_b"))
    assert len(all_msgs) == 1
    assert all_msgs[0].from_agent == "worker_a"
    assert all_msgs[0].read is True
