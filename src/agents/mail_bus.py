"""进程内邮件总线 - Agent 间异步通信"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MailMessage:
    """邮件消息"""
    id: str
    from_agent: str
    to_agent: str
    subject: str
    body: str
    attachments: list[str]
    created_at: str
    read: bool = False


class MailBus:
    """进程内邮件总线，每个任务拥有独立实例，任务结束后销毁"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._lock = asyncio.Lock()
        self._messages: list[MailMessage] = []

    async def send(
        self,
        from_agent: str,
        to_agent: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None,
    ) -> str:
        """发送消息，返回消息 ID"""
        msg = MailMessage(
            id=uuid.uuid4().hex,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
            body=body,
            attachments=attachments or [],
            created_at=datetime.now().isoformat(),
        )
        async with self._lock:
            self._messages.append(msg)
        return msg.id

    async def read_inbox(self, agent_name: str) -> list[MailMessage]:
        """读取未读消息并标记为已读"""
        async with self._lock:
            unread = [m for m in self._messages if m.to_agent == agent_name and not m.read]
            for m in unread:
                m.read = True
        return unread

    async def mark_read(self, message_id: str) -> None:
        """将指定消息标记为已读"""
        async with self._lock:
            for m in self._messages:
                if m.id == message_id:
                    m.read = True
                    break

    async def get_all(self, agent_name: str) -> list[MailMessage]:
        """获取全部消息（含已读）"""
        async with self._lock:
            return [m for m in self._messages if m.to_agent == agent_name]
