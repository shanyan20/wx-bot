"""实验性 Windows UIA 接入，必须配合已验证的客户端控件配置。

不内置猜测的微信控件名称，不使用坐标点击，不根据昵称猜会话。
每个配置会话必须有独立窗口；消息行必须暴露稳定 ID、发送者、正文及 ISO 时间。
TODO(T01/T02): 尚未在用户的微信版本验证；若客户端不提供这些字段，本适配器不能使用。
此时应另写适配器，不能用文本哈希或 UIA runtime_id 冒充稳定的微信消息 ID。
"""

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial

from wechat_bot.domain import Job, Message, NotSentError, SendReceipt, UncertainSendError


class WindowsUIAAdapter:
    def __init__(self, profile: dict):
        if os.name != "nt":
            raise RuntimeError("windows_uia 仅支持 Windows")
        if profile.get("verified") is not True:
            raise ValueError("TODO(T01)：须实机验证 UIA 配置后才能启用 windows.verified")
        self.profile = profile
        self.chats = {chat["id"]: chat for chat in profile.get("chats", [])}
        if not self.chats or len(self.chats) != len(profile["chats"]):
            raise ValueError("windows.chats 不能为空且 id 不可重复")
        required = (
            "id", "window_title", "header_id", "header_text", "list_id", "input_id",
            "send_button_id", "sender_id", "body_id", "timestamp_id", "self_sender",
        )
        for chat in self.chats.values():
            if any(not isinstance(chat.get(key), str) or not chat[key] for key in required):
                raise ValueError("UIA 会话配置缺少必填控件信息，参见 config/windows.example.toml")
            if type(chat.get("process_id")) is not int or chat["process_id"] <= 0:
                raise ValueError("必须提供目标微信进程 process_id")
            if chat.get("chat_type") not in ("private", "group"):
                raise ValueError("chat_type 必须为 private/group")
            if any(chat[key].startswith("TODO_") for key in required):
                raise ValueError("TODO(T01)：必须替换所有 UIA 配置占位符")
        targets = {(c["process_id"], c["window_title"]) for c in self.chats.values()}
        if len(targets) != len(self.chats):
            raise ValueError("不同会话不得绑定同一目标窗口")
        # COM 对象创建、使用与释放都在这一个线程；不使用默认线程池来回切换线程。
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wechat-uia")
        self.desktop = None
        self.seen: dict[str, set[str]] = {}
        self.pending: dict[str, set[str]] = {}

    async def _call(self, method, *args):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, partial(method, *args)
        )

    def _initialize(self):
        if self.desktop is None:
            try:
                import sys

                sys.coinit_flags = 0  # pywinauto 使用 MTA；必须在首次导入前设置。
                import comtypes
                from pywinauto import Desktop
            except ImportError:
                raise RuntimeError('请先安装 Windows 依赖：pip install -e ".[windows]"') from None
            comtypes.CoInitializeEx(0)
            self.desktop = Desktop(backend="uia")

    @staticmethod
    def _one(root, auto_id: str):
        matches = root.descendants(auto_id=auto_id)
        if len(matches) != 1:
            raise ValueError("控件缺失或不唯一")
        return matches[0]

    def _window(self, chat: dict):
        # TODO(T04)：进程 PID 暂由配置提供，未实现登录/账号核对和重启后自动发现。
        self._initialize()
        matches = self.desktop.windows(title=chat["window_title"], process=chat["process_id"])
        if len(matches) != 1:
            raise ValueError("目标窗口缺失或不唯一")
        window = matches[0]
        window.verify_actionable()
        if self._one(window, chat["header_id"]).window_text() != chat["header_text"]:
            raise ValueError("会话标题校验失败")
        return window

    def _snapshot(self, chat: dict) -> list[Message]:
        window = self._window(chat)
        rows = self._one(window, chat["list_id"]).children(control_type="ListItem")
        messages, identities = [], set()
        for row in rows:
            native_id = row.element_info.automation_id
            if not native_id or native_id in identities:
                raise ValueError("TODO(T02)：消息行未暴露唯一稳定 ID")
            identities.add(native_id)
            sender = self._one(row, chat["sender_id"]).window_text()
            body = self._one(row, chat["body_id"]).window_text()
            timestamp = self._one(row, chat["timestamp_id"]).window_text()
            date = datetime.fromisoformat(timestamp)
            if date.tzinfo is None:
                raise ValueError("TODO(T02)：消息时间必须包含时区")
            # TODO(T03)：当前不推断真正的 @，群聊仅通过命令前缀触发。
            # sender 必须经实机验证为稳定用户标识，而不是可能重名的群昵称。
            message = Message(
                source="windows_uia",
                message_id=json.dumps([chat["id"], native_id], ensure_ascii=False),
                conversation_id=chat["id"], sender_id=sender, text=body,
                created_at=date.timestamp(), chat_type=chat["chat_type"],
                is_self=sender == chat["self_sender"], mentioned=False,
            )
            message.validate()
            messages.append(message)
        return messages

    def _poll(self) -> list[Message]:
        snapshots = {key: self._snapshot(chat) for key, chat in self.chats.items()}
        result = []
        self.pending = {}
        for key, messages in snapshots.items():
            identifiers = {message.message_id for message in messages}
            if key not in self.seen:
                # 首次启动/重新创建适配器以当前视图为基线，不自动回复屏幕历史消息。
                # 代价：停机期间的新消息也可能被略过，详见 T02。
                self.seen[key] = identifiers
                continue
            result.extend(m for m in messages if m.message_id not in self.seen[key])
            self.pending[key] = identifiers
        return result

    async def poll(self) -> list[Message]:
        return await self._call(self._poll)

    def _ack(self):
        # 保留当前可见集合；滚动重现的旧消息还会被数据库去重。
        self.seen.update(self.pending)
        self.pending = {}

    async def ack(self, messages: list[Message]) -> None:
        await self._call(self._ack)

    def _write(self, chat, callback):
        """Control-panel subclass inserts its stop/allowlist barrier here."""
        return callback()

    def _send(self, job: Job) -> SendReceipt:
        if job.message.conversation_id not in self.chats:
            raise NotSentError("unknown conversation")
        chat = self.chats[job.message.conversation_id]
        try:
            before = {m.message_id for m in self._snapshot(chat)}
            window = self._window(chat)
            edit = self._one(window, chat["input_id"])
            # 不覆盖用户草稿。不调用快捷键，避免键盘焦点漂移。
            if edit.get_value():
                raise ValueError("输入框已有草稿")
            self._write(chat, lambda: edit.set_edit_text(job.reply))
            window = self._window(chat)  # 输入后再核对目标会话。
            if self._one(window, chat["input_id"]).get_value() != job.reply:
                raise ValueError("输入文本验证失败")
            button = self._one(window, chat["send_button_id"])
        except Exception:
            # 可能留下草稿，但明确没有调用发送按钮；重试遇到草稿会继续拒绝。
            raise NotSentError("UIA preflight failed") from None
        try:
            # 从这里开始任何异常都无法确定是否已发送，包括 invoke 本身抛出的异常。
            self._write(chat, button.invoke)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                for message in self._snapshot(chat):
                    if (message.message_id not in before and message.is_self
                            and message.text == job.reply):
                        return SendReceipt(f"uia_visible:{message.message_id}")
                time.sleep(0.2)
        except Exception:
            raise UncertainSendError("UIA verification failed") from None
        raise UncertainSendError("No matching outgoing UI row")

    async def send(self, job: Job) -> SendReceipt:
        return await self._call(self._send, job)

    def _close(self):
        if self.desktop is not None:
            import comtypes

            self.desktop = None
            comtypes.CoUninitialize()

    async def close(self) -> None:
        try:
            await self._call(self._close)
        finally:
            self.executor.shutdown(wait=True)
