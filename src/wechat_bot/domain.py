"""跨层共享的数据契约；统一使用 UTC Unix 秒，禁止以展示名称代替稳定会话 ID。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Message:
    """适配器必须提供可重放且稳定的 source + message_id。"""

    source: str
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    created_at: float
    chat_type: Literal["private", "group"] = "private"
    is_self: bool = False
    mentioned: bool = False
    kind: str = "text"

    @property
    def session_id(self) -> str:
        # JSON 编码由存储层负责，避免分隔符碰撞造成跨会话上下文泄漏。
        import json

        parts = [self.source, self.chat_type, self.conversation_id]
        if self.chat_type == "group":
            parts.append(self.sender_id)
        return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))

    def validate(self) -> None:
        import math

        for value in (self.source, self.message_id, self.conversation_id, self.sender_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("消息的来源、消息 ID、会话 ID 和发送者 ID 不得为空")
        if self.chat_type not in ("private", "group"):
            raise ValueError("chat_type 必须为 private 或 group")
        if not isinstance(self.text, str) or not isinstance(self.kind, str):
            raise ValueError("text 和 kind 必须是字符串")
        if not all(type(x) is bool for x in (self.is_self, self.mentioned)):
            raise ValueError("消息布尔字段必须是真正的布尔值")
        if type(self.created_at) not in (int, float) or not math.isfinite(self.created_at):
            raise ValueError("created_at 必须为有限时间戳")


@dataclass(frozen=True)
class Job:
    id: int
    message: Message
    prompt: str
    attempts: int
    reply: str | None = None


@dataclass(frozen=True)
class SendReceipt:
    """只表示适配器确认了发送证据，不表示对方已读或服务端交付。"""

    evidence: str


class NotSentError(Exception):
    """适配器确定没有触发发送；可进行有上限的重试。"""


class UncertainSendError(Exception):
    """发送可能已发生，必须停留在 uncertain，禁止自动重发。"""


class ModelError(Exception):
    """模型请求失败；异常文本不应包含令牌、完整提示词或服务端原始响应。"""
