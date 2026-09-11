"""Native DB inbound + pinned UIA outbound for the explicit review workflow."""

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from wechat_bot.acceptance import Packet
from wechat_bot.adapters.native_identity import native_message_key
from wechat_bot.adapters.native_media import resolve_image
from wechat_bot.adapters.sqlcipher_snapshot import decode_snapshot
from wechat_bot.adapters.window_send import click_send_button
from wechat_bot.adapters.windows_uia import WindowsUIAAdapter
from wechat_bot.control import SessionGate

HEADER = ("content_view.top_content_view.title_h_view.left_v_view.left_content_v_view."
          "left_ui_.big_title_line_h_view.current_chat_name_label")


class SnapshotChangedError(ValueError):
    """Transient read-only snapshot conflict, never a reason to click Send again."""


def text_content(value):
    if isinstance(value, bytes):
        if value.startswith(b"\x28\xb5\x2f\xfd"):
            import zstandard
            value = zstandard.ZstdDecompressor().decompress(value, max_output_size=1024 * 1024)
        return value.decode("utf-8")
    return value if isinstance(value, str) else ""


class NativeReview:
    def __init__(self, project):
        import win32crypt

        self.project = Path(project)
        _, value = win32crypt.CryptUnprotectData(
            (self.project / "data/db-probe/keys.dpapi").read_bytes(), None, None, None, 1)
        self.binding = json.loads(value)
        self.root = Path(self.binding["root"]).resolve()
        self.self_id = re.sub(r"_[a-zA-Z0-9]{4}$", "", self.root.parent.name)
        self.targets, self.cursors, self.cache = {}, {}, {}
        self.gate = SessionGate(set())
        self.uia = WindowsUIAAdapter.__new__(WindowsUIAAdapter)
        self.uia.desktop = None
        self.stopped = True

    def check_process(self):
        import psutil
        if psutil.Process(self.binding["pid"]).create_time() != self.binding["started"]:
            raise ValueError("微信已重启，请重新校准密钥和窗口")

    def candidates(self):
        from pywinauto import Desktop
        self.check_process()
        results = []
        for window in Desktop(backend="uia").windows(process=self.binding["pid"]):
            aid = window.element_info.automation_id
            prefix = next((p for p in ("ChatSingleWindow", "ChatGroupWindow")
                           if aid.startswith(p)), None)
            if not prefix:
                continue
            conversation = aid[len(prefix):]
            if not conversation or not window.window_text():
                continue
            results.append({"id": conversation, "chat_type": "group" if conversation.endswith(
                "@chatroom") else "private", "window_title": window.window_text(),
                "process_id": self.binding["pid"], "hwnd": window.handle, "root_id": aid,
                "header_id": HEADER, "header_text": window.window_text(),
                "input_id": "chat_input_field", "send_button_id": "",
                "send_button_name": "发送", "send_button_class": "mmui::XOutlineButton",
                "send_scope_id": "chat_message_page"})
        return results

    def window(self, chat):
        from pywinauto import Desktop
        self.check_process()
        self.gate.read(chat)
        window = Desktop(backend="uia").window(handle=chat["hwnd"]).wrapper_object()
        if (window.element_info.process_id != chat["process_id"]
                or window.element_info.automation_id != chat["root_id"]
                or window.window_text() != chat["window_title"]
                or self.uia._one(window, HEADER).window_text() != chat["header_text"]):
            raise ValueError("目标聊天身份改变，拒绝操作")
        return window

    def prepare_connect(self, chats):
        """Called synchronously by the UI so Stop cannot be undone by a queued connect."""
        if not chats or len({c["id"] for c in chats}) != len(chats):
            raise ValueError("白名单为空或重复")
        self.targets = {c["id"]: dict(c) for c in chats}
        self.gate = SessionGate({(c["chat_type"], c["id"]) for c in chats})
        self.cursors = {}
        self.stopped = False

    def connect(self):
        for chat in self.targets.values():
            self.window(chat)
            rows = self.read_rows(chat)
            self.cursors[chat["id"]] = {r["key"] for r in rows}
        self.gate.enable()

    def stop(self):
        self.stopped = True
        self.gate.request_stop()

    def database(self, path, secret):
        wal_path = Path(str(path) + "-wal")
        signature = (path.stat().st_size, path.stat().st_mtime_ns,
                     wal_path.stat().st_size if wal_path.exists() else 0,
                     wal_path.stat().st_mtime_ns if wal_path.exists() else 0)
        if path in self.cache and self.cache[path][0] == signature:
            return self.cache[path][1]
        data = path.read_bytes()
        wal = wal_path.read_bytes() if wal_path.exists() else b""
        if (hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest()
                or (wal_path.read_bytes() if wal_path.exists() else b"") != wal):
            raise SnapshotChangedError("微信数据库正在变化，快照需要重新读取")
        decoded, _ = decode_snapshot(data, wal, bytes.fromhex(secret["key"]),
                                     bytes.fromhex(secret["salt"]))
        conn = sqlite3.connect(":memory:")
        conn.deserialize(decoded)
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            conn.close()
            raise ValueError("微信数据库快照验证失败")
        if path in self.cache:
            self.cache[path][1].close()
        self.cache[path] = signature, conn
        return conn

    def read_rows(self, chat):
        self.window(chat)
        table = "Msg_" + hashlib.md5(chat["id"].encode()).hexdigest()
        result = []
        found = False
        for source, secret in self.binding["keys"].items():
            path = Path(source).resolve()
            if path.parent != self.root / "message":
                raise ValueError("数据库不在已绑定目录")
            conn = self.database(path, secret)
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'",
                                (table,)).fetchone():
                continue
            found = True
            if not conn.execute('SELECT 1 FROM Name2Id WHERE user_name=?',
                                (self.self_id,)).fetchone():
                raise ValueError("当前账号原生身份尚未匹配数据库，不能推断自己发送的消息")
            rows = conn.execute(f'SELECT local_id,server_id,real_sender_id,create_time,local_type,'
                                f'message_content,compress_content,packed_info_data FROM "{table}" '
                                'ORDER BY local_id DESC LIMIT 200').fetchall()
            for row in rows:
                sender = conn.execute('SELECT user_name FROM Name2Id WHERE rowid=?',
                                      (row[2],)).fetchone()
                if not sender or not sender[0]:
                    raise ValueError("消息发送者无法确认")
                key = native_message_key(account=self.root.parent.name,
                                         generation=hashlib.sha256(bytes.fromhex(
                                             secret["salt"])).hexdigest(), shard=path.name,
                                         conversation=chat["id"], table=table, local_id=row[0])
                result.append(dict(key=key, local_id=row[0], server_id=row[1], sender=sender[0],
                                   timestamp=row[3], kind=row[4] & 0xFFFFFFFF,
                                   content=row[5], compressed=row[6], packed=row[7]))
        self.window(chat)
        if not found:
            raise ValueError("已授权消息分片中未找到该白名单会话；需要单独校准分片")
        return sorted(result, key=lambda r: (r["timestamp"], r["local_id"], r["key"]))

    def packet(self, chat, row):
        kind = {1: "text", 3: "image"}.get(row["kind"], "unsupported")
        text, image, note = "", "", ""
        try:
            if kind == "text":
                text = text_content(row["content"] or row["compressed"])
                if chat["chat_type"] == "group" and text.startswith(row["sender"] + ":\n"):
                    text = text[len(row["sender"]) + 2:]
            elif kind == "image":
                image, note = resolve_image(
                    self.root.parent, chat["id"], [row["content"], row["packed"]],
                    self.project / "data/acceptance/media", pid=self.binding["pid"],
                    check=lambda: self.gate.read(chat))
            else:
                note = f"未支持的微信消息类型：{row['kind']}"
        except (ValueError, OSError, UnicodeError) as exc:
            note = str(exc)
        self.window(chat)
        return Packet(row["key"], self.root.parent.name, chat["id"], chat["chat_type"],
                      row["sender"], row["timestamp"], kind, text, image, note)

    def poll(self):
        packets = []
        for chat in self.targets.values():
            rows = self.read_rows(chat)
            previous = self.cursors[chat["id"]]
            current = {r["key"] for r in rows}
            if previous and not previous & current:
                raise ValueError("消息连续性丢失，拒绝自动处理历史记录")
            for row in rows:
                if row["key"] not in previous and row["sender"] != self.self_id:
                    packets.append(self.packet(chat, row))
            self.cursors[chat["id"]] = previous | current
        return packets

    def latest(self, conversation, kind=None):
        chat = self.targets[conversation]
        rows = [r for r in self.read_rows(chat) if r["sender"] != self.self_id
                and (kind is None or r["kind"] == kind)]
        if not rows:
            raise ValueError("目标聊天没有可读取的对方消息")
        return self.packet(chat, rows[-1])

    def reload(self, conversation, key):
        chat = self.targets[conversation]
        rows = [r for r in self.read_rows(chat)
                if r["key"] == key and r["sender"] != self.self_id]
        if len(rows) != 1:
            raise ValueError("原样本已不可唯一读取，请使用新测试消息")
        return self.packet(chat, rows[0])

    def draft(self, conversation, reply):
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("拒绝输入空回复")
        chat = self.targets[conversation]

        def action():
            edit = self.uia._one(self.window(chat), chat["input_id"])
            if edit.get_value():
                raise ValueError("微信输入框已有草稿，拒绝覆盖")
            edit.set_edit_text(reply)
            if self.uia._one(self.window(chat), chat["input_id"]).get_value() != reply:
                raise ValueError("草稿回读不一致，未发送")
            return "已填入并回读一致；尚未发送"
        return self.gate.action(chat, action)

    def send(self, conversation, reply):
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("拒绝发送空回复")
        chat = self.targets[conversation]
        self.invalidate_snapshots()
        before = {r["key"] for r in self.read_rows(chat)}

        def action():
            window = self.window(chat)
            if self.uia._one(window, chat["input_id"]).get_value() != reply:
                raise ValueError("草稿改变，拒绝发送")
            button = self.uia._send_button(window, chat, actionable=True)
            return click_send_button(window, button, chat["process_id"])
        dispatch = self.gate.action(chat, action)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not self.stopped:
            self.invalidate_snapshots()  # do not rely on cached file timestamps for send evidence
            try:
                rows = self.read_rows(chat)
            except SnapshotChangedError:
                time.sleep(0.5)
                continue
            matches = [row for row in rows
                       if row["key"] not in before and row["sender"] == self.self_id
                       and row["kind"] == 1
                       and text_content(row["content"] or row["compressed"]).replace(
                           "\r\n", "\n") == reply.replace("\r\n", "\n")]
            if len(matches) > 1:
                raise ValueError("多条同文出站记录，无法唯一确认本次发送；禁止自动重发")
            if len(matches) == 1:
                return ("native_db_outgoing:" + matches[0]["key"]
                        + "\n发送方式：" + dispatch["method"])
            time.sleep(0.5)
        draft_empty = not self.uia._one(self.window(chat), chat["input_id"]).get_value()
        raise ValueError("已投递一次窗口点击，但未找到对应出站记录；"
                         f"输入框已清空={draft_empty}。结果不确定，禁止自动重发")

    def invalidate_snapshots(self):
        for _, conn in self.cache.values():
            conn.close()
        self.cache.clear()

    def close(self):
        self.stop()
        self.invalidate_snapshots()
