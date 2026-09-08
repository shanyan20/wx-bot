"""纯业务规则：不操作窗口、不调用模型、不读写数据库，便于独立测试。"""

from dataclasses import dataclass

from wechat_bot.config import Settings
from wechat_bot.domain import Message


@dataclass(frozen=True)
class Decision:
    accept: bool
    prompt: str = ""
    reason: str = ""


def decide(message: Message, settings: Settings, now: float) -> Decision:
    if message.is_self:
        return Decision(False, reason="self_message")
    if message.kind != "text":
        return Decision(False, reason="unsupported_kind")
    if message.created_at > now + 60:
        return Decision(False, reason="future_timestamp")
    if now - message.created_at > settings.message_ttl_seconds:
        return Decision(False, reason="expired_on_receive")
    allowed = (
        settings.group_allowlist if message.chat_type == "group" else settings.private_allowlist
    )
    if message.conversation_id not in allowed:
        return Decision(False, reason="not_allowlisted")
    prompt = message.text.strip()
    if message.chat_type == "group":
        if prompt.startswith(settings.group_prefix):
            prompt = prompt[len(settings.group_prefix):].strip()
        elif not message.mentioned:
            return Decision(False, reason="group_not_triggered")
    if not prompt:
        return Decision(False, reason="empty_text")
    if len(prompt) > settings.max_input_chars:
        return Decision(False, reason="input_too_long")
    return Decision(True, prompt=prompt)

