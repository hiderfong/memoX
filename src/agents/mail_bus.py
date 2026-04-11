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
    created_at: str
    read: bool = False
    attachments: list[str] = field(default_factory=list)


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

    async def get_history(self) -> list[MailMessage]:
        """获取该任务的全部邮件通信记录，按发送时间排序"""
        async with self._lock:
            return sorted(self._messages, key=lambda m: m.created_at)

    async def export_log(self) -> str:
        """导出格式化的邮件通信日志"""
        messages = await self.get_history()
        if not messages:
            return "(无邮件通信记录)"

        lines = [f"=== 邮件通信日志 (task: {self.task_id}) ===", f"共 {len(messages)} 封邮件", ""]
        for i, msg in enumerate(messages, 1):
            status = "已读" if msg.read else "未读"
            lines.append(f"--- 邮件 #{i} [{status}] ---")
            lines.append(f"  时间: {msg.created_at}")
            lines.append(f"  发件人: {msg.from_agent}")
            lines.append(f"  收件人: {msg.to_agent}")
            lines.append(f"  主题: {msg.subject}")
            lines.append(f"  正文: {msg.body}")
            if msg.attachments:
                lines.append(f"  附件: {', '.join(msg.attachments)}")
            lines.append("")
        return "\n".join(lines)
