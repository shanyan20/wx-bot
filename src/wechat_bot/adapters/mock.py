"""可执行的本地接入：JSONL 入站，JSONL 出站。不连接微信，不发送真实消息。"""

import json
import os
from dataclasses import asdict
from pathlib import Path

from wechat_bot.domain import Job, Message, SendReceipt


class MockAdapter:
    def __init__(self, inbox: Path, outbox: Path):
        self.inbox = inbox
        self.outbox = outbox
        self.offset = 0
        self.pending_offset = 0

    async def poll(self) -> list[Message]:
        if not self.inbox.exists():
            return []
        # 每次启动从头重放，由数据库去重。只读取完整行，支持外部追加时的半行写入。
        result = []
        with self.inbox.open("rb") as stream:
            stream.seek(self.offset)
            self.pending_offset = self.offset
            for _ in range(100):
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break
                if line.strip():
                    message = Message(**json.loads(line))
                    message.validate()
                    result.append(message)
                self.pending_offset = stream.tell()
        return result

    async def ack(self, messages: list[Message]) -> None:
        self.offset = self.pending_offset

    async def send(self, job: Job) -> SendReceipt:
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        # 模拟适配器支持本地幂等键；真实 UIA 不能据此宣称 exactly-once。
        key = f"job-{job.id}"
        if self.outbox.exists():
            for line in self.outbox.read_text(encoding="utf-8").splitlines():
                if json.loads(line)["idempotency_key"] == key:
                    return SendReceipt(f"mock:{key}")
        record = {
            "idempotency_key": key,
            "conversation_id": job.message.conversation_id,
            "reply": job.reply,
            "in_reply_to": job.message.message_id,
        }
        with self.outbox.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return SendReceipt(f"mock:{key}")

    async def close(self) -> None:
        pass


def append_message(path: Path, message: Message) -> None:
    """示例工具；mock inbox 是单写入者追加文件，不支持轮换或原地截断。"""
    message.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")

