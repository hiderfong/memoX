"""Agent 间通信协议 — InterAgentMessage 标准结构"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any
import uuid


class MessagePriority(IntEnum):
    """消息优先级，数值越高越紧急"""
    LOW = 1
    NORMAL = 3
    HIGH = 5
    URGENT = 7


@dataclass
class ToolResult:
    """工具执行结果，可作为消息附件"""
    tool_name: str
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class InterAgentMessage:
    """Agent 间标准消息格式

    用法示例:
        # Agent A 广播消息给所有 Agent
        msg = InterAgentMessage.broadcast(
            sender="researcher",
            content="搜索完成，结果: ...",
            priority=MessagePriority.NORMAL,
        )

        # Agent B 点对点发送
        msg = InterAgentMessage(
            sender="writer",
            receiver="reviewer",
            content="草稿已完成，请审阅",
            priority=MessagePriority.HIGH,
        )

        await mail_bus.send_inter_agent(msg)
    """
    sender: str                          # 发送者 worker 名称
    receiver: str | None                 # 接收者，None = 广播
    content: str                          # 消息主体
    attachments: list[ToolResult] = field(default_factory=list)  # 附件（工具输出）
    reply_to: str | None = None           # 回复某条消息的 ID
    priority: MessagePriority = MessagePriority.NORMAL
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def broadcast(
        cls,
        sender: str,
        content: str,
        attachments: list[ToolResult] | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> "InterAgentMessage":
        """创建广播消息（receiver=None）"""
        return cls(
            sender=sender,
            receiver=None,
            content=content,
            attachments=attachments or [],
            priority=priority,
        )

    def is_broadcast(self) -> bool:
        return self.receiver is None

    def to_summary(self) -> str:
        """人类可读的消息摘要（用于日志）"""
        prefix = "[BROADCAST]" if self.is_broadcast() else f"[{self.sender}→{self.receiver}]"
        attach_info = f" +{len(self.attachments)} attachments" if self.attachments else ""
        return f"{prefix} [{self.priority.name}] {self.content[:80]}{attach_info}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "receiver": self.receiver,
            "content": self.content,
            "attachments": [a.to_dict() for a in self.attachments],
            "reply_to": self.reply_to,
            "priority": self.priority.value,
            "created_at": self.created_at,
        }
