"""Thread-safe, default-off control plane for a strictly scoped UIA test session."""

import asyncio
import copy
import json
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

from wechat_bot.adapters.windows_uia import WindowsUIAAdapter
from wechat_bot.domain import NotSentError
from wechat_bot.engine import Engine
from wechat_bot.locking import InstanceLock
from wechat_bot.services.model import EchoModel, HttpModel
from wechat_bot.storage import Store


class SessionGate:
    """Stop returns only after any action already inside the lock finishes.

    request_stop is nonblocking: the GUI remains responsive even if native UIA hangs.
    A native action already invoked cannot be undone; never report OFF until drained.
    """

    def __init__(self, targets):
        self.targets = frozenset(targets)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.enabled = False

    def enable(self):
        with self.lock:
            if self.stopping.is_set() or not self.targets:
                raise ValueError("会话已停止或白名单为空，请重新启动")
            self.enabled = True

    def request_stop(self):
        self.stopping.set()

    def stop(self):
        self.request_stop()
        with self.lock:
            self.enabled = False

    def read(self, chat):
        if self.stopping.is_set() or (chat["chat_type"], chat["id"]) not in self.targets:
            raise NotSentError("接入已停止或会话不在白名单")

    def action(self, chat, callback):
        with self.lock:
            self.read(chat)
            if not self.enabled:
                raise NotSentError("Bot 关闭，只读检查不允许写入")
            return callback()


class ControlledUIAAdapter(WindowsUIAAdapter):
    """Only selected independent windows; pin HWND for the lifetime of one run."""

    def __init__(self, profile, gate):
        super().__init__(profile)
        self.gate = gate
        self.handles = {}

    def _window(self, chat):
        self.gate.read(chat)
        window = super()._window(chat)
        handle = window.handle
        if not handle:
            raise ValueError("独立聊天窗口没有有效句柄")
        previous = self.handles.setdefault(chat["id"], handle)
        if previous != handle:
            raise ValueError("窗口已重建，请关闭 Bot 并重新校准")
        return window

    def _write(self, chat, callback):
        def checked():
            self._window(chat)  # final identity check inside the stop barrier
            return callback()
        return self.gate.action(chat, checked)

    def _preflight(self):
        counts = []
        for chat in self.chats.values():
            messages = self._snapshot(chat)
            window = self._window(chat)
            edit = self._one(window, chat["input_id"])
            if edit.get_value():
                raise ValueError("测试聊天存在草稿，请自行处理后再检查")
            # Read interface capabilities, never set text or invoke during preflight.
            if not callable(getattr(edit, "set_edit_text", None)):
                raise ValueError("输入框不支持 UIA 文本接口")
            button = self._one(window, chat["send_button_id"])
            if button.iface_invoke is None:
                raise ValueError("发送按钮不支持 UIA Invoke")
            counts.append(len(messages))
        return counts


def select_settings(base, targets, run_dir, echo=True):
    """Whitelist enforced before desktop access, including reads and model input."""
    if base.adapter != "windows_uia":
        raise ValueError("控制面板仅支持真实 windows_uia 接入")
    targets = frozenset(targets)
    if not targets:
        raise ValueError("请至少选择一个测试联系人或群聊")
    profile = copy.deepcopy(base.windows)
    chats = profile.get("chats", [])
    keys = [(chat.get("chat_type"), chat.get("id")) for chat in chats]
    if len(keys) != len(set(keys)) or len({key[1] for key in keys}) != len(keys):
        raise ValueError("联系人和群聊的配置 ID 必须全局唯一")
    if not targets.issubset(set(keys)):
        raise ValueError("白名单包含未登记的会话")
    profile["chats"] = [chat for chat in chats if (chat["chat_type"], chat["id"]) in targets]
    profile["verified"] = all(
        chat.get("verified", profile.get("verified")) is True for chat in profile["chats"]
    )
    root = Path(run_dir)
    return replace(
        base, windows=profile,
        private_allowlist=frozenset(key for kind, key in targets if kind == "private"),
        group_allowlist=frozenset(key for kind, key in targets if kind == "group"),
        model="echo" if echo else base.model,
        database=root / "bot.sqlite3", logs=root / "bot.log",
        heartbeat=root / "heartbeat.json", pause_file=root / "PAUSE",
        inbox=root / "unused-inbox.jsonl", outbox=root / "unused-outbox.jsonl",
    )


def save_selection(path, targets):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(sorted(targets), ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_selection(path):
    if not path.exists():
        return frozenset()
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(
        not isinstance(row, list) or len(row) != 2 or row[0] not in ("private", "group")
        or not isinstance(row[1], str) or not row[1] for row in rows
    ):
        raise ValueError("保存的白名单损坏，请重新选择")
    return frozenset(map(tuple, rows))


class Controller:
    def __init__(self, settings, data_dir, notify):
        self.settings, self.data_dir, self.notify = settings, Path(data_dir), notify
        self.thread = None
        self.gate = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def launch(self, targets, *, inspect_only=False, echo=True):
        if self.busy:
            raise ValueError("请等待当前检查或停止完成")
        run_dir = self.data_dir / "runs" / uuid.uuid4().hex
        settings = select_settings(self.settings, targets, run_dir, echo)
        # Pure profile validation: return actionable errors before starting the UIA thread.
        validation = WindowsUIAAdapter(settings.windows)
        validation.executor.shutdown(wait=True)
        gate = SessionGate(targets)
        self.gate = gate
        self.thread = threading.Thread(
            target=self._worker, args=(settings, gate, inspect_only), daemon=True,
            name="wechat-control",
        )
        self.thread.start()

    def stop(self):
        if self.gate:
            self.gate.request_stop()

    def _worker(self, settings, gate, inspect_only):
        try:
            # Same control directory permits only one panel session, across run DBs.
            with InstanceLock(self.data_dir / "session.lock"):
                asyncio.run(self._run(settings, gate, inspect_only))
        except Exception as exc:
            # Third-party errors may contain chat text. Publish category only.
            self.notify("error", f"检查/运行未通过：{type(exc).__name__}。请校准配置与独立窗口。")
        finally:
            gate.stop()
            self.notify("stopped", "Bot 已关闭；没有继续运行的收发任务")

    async def _run(self, settings, gate, inspect_only):
        adapter = ControlledUIAAdapter(settings.windows, gate)
        engine, store, model = None, None, None
        try:
            self.notify("checking", "只读检查所选窗口、消息字段与输入接口…")
            await adapter._call(adapter._preflight)
            if inspect_only:
                self.notify("checked", "只读检查通过；尚未证明消息 ID 稳定或对端收到")
                return
            if gate.stopping.is_set():
                return
            # Establish a fresh baseline before enabling writes. Never recover old run queues.
            await adapter.poll()
            model = EchoModel() if settings.model == "echo" else HttpModel(settings)
            store = Store(settings.database)
            engine = Engine(settings, store, adapter, model)
            gate.enable()
            self.notify("running", "Bot 运行中 · 仅处理本次白名单内的新消息")
            while not gate.stopping.is_set():
                await engine.tick()
                counts = store.status(time.time())["counts"]
                self.notify("counts", json.dumps(counts, ensure_ascii=False))
                if engine.poll_failures or counts.get("uncertain") or counts.get("failed"):
                    self.notify("error", "接入故障或发送未确认，已自动停止；请人工核对。")
                    gate.request_stop()
                    break
                await asyncio.sleep(min(settings.poll_seconds, 0.2))
        finally:
            gate.request_stop()
            try:
                if engine is not None:
                    # Pending UI writes fail even after model completion.
                    await engine.close()
                else:
                    try:
                        await adapter.close()
                    finally:
                        if model is not None:
                            await model.close()
            finally:
                if store is not None:
                    try:
                        store.cancel_queued(time.time())
                    finally:
                        store.close()
