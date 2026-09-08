"""交互式好友模拟：一行输入视为一条私聊消息，复用 Engine/Store/ReplyModel。

每次运行创建独立数据目录；/reset 切换会话键，使之后的请求不再读取旧上下文。
为了让演示易于观察，每次等待当前消息结束后才接受下一行输入。
"""

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from wechat_bot.adapters.console import ConsoleAdapter
from wechat_bot.config import Settings, load_settings
from wechat_bot.domain import Message
from wechat_bot.engine import Engine
from wechat_bot.services.model import EchoModel, HttpModel, ReplyModel
from wechat_bot.storage import Store

TERMINAL_STATES = {"sent", "ignored", "failed", "expired", "canceled", "uncertain"}
HELP = "输入文字与 bot 对话；/reset 开始新对话；/help 帮助；/quit 或 /exit 退出。"


class ConsoleSession:
    def __init__(self, settings: Settings, directory: Path, model: ReplyModel | None = None):
        self.conversation_id = "console-" + uuid.uuid4().hex
        # 仅对本次演示覆盖接入/数据路径和白名单，不修改用户 TOML 或正式机器人数据库。
        self.settings = replace(
            settings, adapter="console", database=directory / "bot.sqlite3",
            logs=directory / "bot.log", heartbeat=directory / "heartbeat.json",
            pause_file=directory / "PAUSE", inbox=directory / "unused-inbox.jsonl",
            outbox=directory / "unused-outbox.jsonl", poll_seconds=0.05,
            private_allowlist=frozenset({self.conversation_id}), group_allowlist=frozenset(),
        )
        self.adapter = ConsoleAdapter()
        self.model = model or (
            HttpModel(self.settings) if settings.model == "http" else EchoModel()
        )
        self.store = Store(self.settings.database)
        self.engine = Engine(self.settings, self.store, self.adapter, self.model)

    def reset(self) -> None:
        """只在没有正在等待的 ask 时调用；保留旧记录，但新会话不再读取它们。"""
        self.conversation_id = "console-" + uuid.uuid4().hex
        self.settings = replace(self.settings, private_allowlist=frozenset({self.conversation_id}))
        self.engine.settings = self.settings

    async def ask(self, text: str) -> dict:
        message = Message(
            source="console", message_id=uuid.uuid4().hex,
            conversation_id=self.conversation_id, sender_id="terminal-friend",
            text=text, created_at=time.time(), chat_type="private",
        )
        self.adapter.submit(message)
        while True:
            await self.engine.tick()
            result = self.store.message_result(message.source, message.message_id)
            if result and result["state"] in TERMINAL_STATES:
                return result
            await asyncio.sleep(self.settings.poll_seconds)

    async def close(self) -> None:
        try:
            await self.engine.close()
        finally:
            self.store.close()


def safe_display(text: str) -> str:
    """保留普通文本和换行，过滤终端控制字符（例如模型返回的 ANSI ESC）。"""
    return "".join(char for char in text if char.isprintable() or char in "\n\t")


async def chat(settings: Settings, *, echo: bool = False, session_root: Path | None = None) -> None:
    if echo:
        settings = replace(settings, model="echo")
    root = session_root or settings.database.parent / "console"
    directory = root / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    session = ConsoleSession(settings, directory)
    print("好友聊天模拟（文本，不连接微信）", flush=True)
    print(f"模型：{settings.model_name if settings.model == 'http' else 'Echo（离线回显）'}")
    print(f"本次记录：{directory.resolve()}")
    print(HELP, flush=True)
    try:
        while True:
            try:
                # 此时没有进行中的业务任务；同步 input 不会阻塞模型生成或消息发送。
                text = input("好友 > ").strip()
            except EOFError:
                break
            if not text:
                continue
            command = text.lower()
            if command in {"/quit", "/exit"}:
                break
            if command == "/help":
                print(HELP, flush=True)
                continue
            if command == "/reset":
                session.reset()
                print("已开始新对话，后续回复不再使用旧上下文。", flush=True)
                continue
            print("bot 正在回复…", flush=True)
            result = await session.ask(text)
            if result["state"] == "sent":
                if result["reason"] == "model_fallback":
                    print("提示：模型请求失败，以下为预设兜底回复。", flush=True)
                print("bot  > " + safe_display(result["reply"]), flush=True)
            else:
                print(f"未回复：{result['state']}（{result['reason']}）", flush=True)
    finally:
        await session.close()
        print("对话已结束。", flush=True)


def main(*, default_config: Path | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    if not sys.stdin.isatty() and hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="把命令行当作好友，与 bot 连续对话")
    parser.add_argument(
        "--config", type=Path,
        default=default_config if default_config is not None else Path("config/deepseek.toml"),
    )
    parser.add_argument("--echo", action="store_true", help="使用离线回显，不调用 DeepSeek")
    parser.add_argument("--session-root", type=Path, help="测试记录目录，默认 data/console")
    args = parser.parse_args()
    try:
        asyncio.run(chat(
            load_settings(args.config), echo=args.echo, session_root=args.session_root,
        ))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print(f"对话失败：{detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
