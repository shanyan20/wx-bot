"""适配器契约。poll 必须允许重放；核心在消息提交数据库后才调用 ack。"""

from typing import Protocol

from wechat_bot.domain import Job, Message, SendReceipt


class ChatAdapter(Protocol):
    async def poll(self) -> list[Message]: ...

    async def ack(self, messages: list[Message]) -> None:
        """确认整个批次已入库。异常或进程退出前未 ack 的消息应能再次读取。"""
        ...

    async def send(self, job: Job) -> SendReceipt:
        """确定未发送抛 NotSentError；其余发送异常必须按结果不确定处理。"""
        ...

    async def close(self) -> None: ...

