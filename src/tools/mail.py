"""邮件工具 - 包装 MailBus，供 Worker Agent 调用"""

from typing import Any

from agents.base_agent import BaseTool
from agents.mail_bus import MailBus


class SendMailTool(BaseTool):
    """向其他 Agent 发送邮件"""

    def __init__(self, agent_name: str, mail_bus: MailBus):
        self._agent_name = agent_name
        self._mail_bus = mail_bus

    @property
    def name(self) -> str:
        return "send_mail"

    @property
    def description(self) -> str:
        return "向其他 Agent（或 coordinator）发送邮件。attachments 传沙箱内绝对路径，接收方用 read_file 读取。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人 agent 名称（如 coordinator）"},
                "subject": {"type": "string", "description": "邮件主题"},
                "body": {"type": "string", "description": "邮件正文"},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "文件路径列表（沙箱内绝对路径）",
                },
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(self, arguments: dict) -> Any:
        msg_id = await self._mail_bus.send(
            from_agent=self._agent_name,
            to_agent=arguments["to"],
            subject=arguments["subject"],
            body=arguments["body"],
            attachments=arguments.get("attachments", []),
        )
        return f"邮件已发送，ID: {msg_id}"


class ReadMailTool(BaseTool):
    """读取自己的未读邮件"""

    def __init__(self, agent_name: str, mail_bus: MailBus):
        self._agent_name = agent_name
        self._mail_bus = mail_bus

    @property
    def name(self) -> str:
        return "read_mail"

    @property
    def description(self) -> str:
        return "读取自己的未读邮件，读取后自动标记为已读。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, arguments: dict) -> Any:
        messages = await self._mail_bus.read_inbox(self._agent_name)
        if not messages:
            return "(无未读邮件)"

        parts = []
        for msg in messages:
            part = (
                f"=== 邮件 ID: {msg.id} ===\n"
                f"发件人: {msg.from_agent}\n"
                f"主题: {msg.subject}\n"
                f"时间: {msg.created_at}\n\n"
                f"{msg.body}"
            )
            if msg.attachments:
                part += f"\n\n附件: {', '.join(msg.attachments)}"
            parts.append(part)

        return "\n\n".join(parts)
