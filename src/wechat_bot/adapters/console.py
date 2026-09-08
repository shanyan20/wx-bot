"""内存中的终端好友适配器；消息仍由 Engine 写入 SQLite 并经过完整调度。

这里只模拟微信的收发边界，不连接微信、不调用 UIA。
终端在任务进入 sent 后显示数据库里的回复，避免显示了结果却没有提交状态。
"""

from wechat_bot.domain import Job, Message, SendReceipt


class ConsoleAdapter:
    def __init__(self):
        self.pending: list[Message] = []

    def submit(self, message: Message) -> None:
        message.validate()
        self.pending.append(message)

    async def poll(self) -> list[Message]:
        return list(self.pending)

    async def ack(self, messages: list[Message]) -> None:
        acknowledged = {message.message_id for message in messages}
        self.pending = [m for m in self.pending if m.message_id not in acknowledged]

    async def send(self, job: Job) -> SendReceipt:
        return SendReceipt(f"console:{job.id}")

    async def close(self) -> None:
        self.pending.clear()

