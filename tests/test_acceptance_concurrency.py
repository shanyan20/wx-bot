"""Exercise the actual Tk scheduler with overlapping model calls and native work."""

import asyncio
import json
import os
import threading
import time
import tkinter as tk

import pytest

from wechat_bot.acceptance import Packet, ReviewCase
from wechat_bot.control import SessionGate


@pytest.mark.skipif(os.name != "nt" and not os.environ.get("DISPLAY"), reason="No GUI display")
@pytest.mark.parametrize("outcome", ["success", "stop", "model_error"])
def test_slow_contact_does_not_block_poll_or_other_contact(tmp_path, monkeypatch, outcome):
    from wechat_bot import acceptance_panel as module

    (tmp_path / "config").mkdir()
    (tmp_path / "config/deepseek.toml").write_text('[model]\nprovider="echo"\n')
    (tmp_path / "data/acceptance").mkdir(parents=True)
    (tmp_path / "data/acceptance/test_contacts.json").write_text(json.dumps({
        "shanyan": "A", "憨憨的小憨憨": "B"}), encoding="utf-8")
    started, release = threading.Event(), threading.Event()
    calls, histories, model_threads, native_threads, errors = [], {}, {}, set(), []
    inbox = []

    def packet(key, contact):
        return Packet(key, "account", contact, "private", contact, 100, "text", key)

    class Backend:
        def __init__(self, project):
            self.targets = {}
            self.gate = SessionGate(set())

        def candidates(self):
            return [dict(id="A", chat_type="private", window_title="shanyan"),
                    dict(id="B", chat_type="private", window_title="憨憨的小憨憨")]

        def prepare_connect(self, chats):
            self.targets = {c["id"]: c for c in chats}
            self.gate = SessionGate({("private", c["id"]) for c in chats})

        def connect(self):
            native_threads.add(threading.get_ident())
            self.gate.enable()

        def poll(self):
            native_threads.add(threading.get_ident())
            rows = list(inbox)
            inbox.clear()
            calls.append(("poll",))
            return rows

        def draft(self, contact, reply):
            native_threads.add(threading.get_ident())
            self.gate.action(self.targets[contact], lambda: calls.append(("draft", contact, reply)))
            return "readback matches"

        def send(self, contact, reply):
            native_threads.add(threading.get_ident())
            self.gate.action(self.targets[contact], lambda: calls.append(("send", contact, reply)))
            return "native_db_outgoing:" + reply

        def stop(self):
            self.gate.request_stop()

        def close(self):
            self.stop()

    class Model:
        def __init__(self, settings):
            pass

        async def reply(self, prompt, history, images=None):
            histories[prompt] = history
            model_threads[prompt] = threading.get_ident()
            calls.append(("model", prompt))
            if prompt == "A1":
                started.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
                if outcome == "model_error":
                    raise TimeoutError("synthetic slow request failed")
            return "reply-" + prompt

        async def close(self):
            pass

    monkeypatch.setattr(module, "HttpModel", Model)
    def initialize(self):
        backend = Backend(self.project)
        return backend, backend.candidates()
    monkeypatch.setattr(module.AcceptancePanel, "initialize", initialize)
    monkeypatch.setattr(module.messagebox, "showerror", lambda *a, **k: errors.append(a))
    def unexpected_confirmation(*args, **kwargs):
        pytest.fail("Automatic replies must not request confirmation")
    monkeypatch.setattr(module.messagebox, "askyesno", unexpected_confirmation)
    root = tk.Tk()
    root.withdraw()
    panel = module.AcceptancePanel(root, tmp_path)

    def until(predicate):
        deadline = time.monotonic() + 6
        while not predicate() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert predicate(), (calls, [(k, c.phase) for k, c in panel.cases.items()])

    try:
        until(lambda: not panel.busy)
        panel.connect()
        until(lambda: panel.connected)
        old = ReviewCase(packet("old-B", "B"), phase="c_review",
                         evidence="native_db_outgoing:old-verified", reply="old")
        panel.mount_case(old)
        panel.add_packet(packet("A1", "A"))
        panel.add_packet(packet("A2", "A"))
        panel.advance()
        until(started.is_set)
        # B arrives via polling WHILE A's model request is deliberately blocked.
        inbox.append(packet("B1", "B"))
        panel.poll()
        until(lambda: ("send", "B", "reply-B1") in calls)
        assert not release.is_set() and "A2" not in histories
        assert histories["A1"] == histories["B1"] == []
        assert model_threads["A1"] != model_threads["B1"]
        assert len(native_threads) == 1
        assert native_threads.isdisjoint(model_threads.values())
        assert old.phase == "c_review" and not old.c_pass
        if outcome == "stop":
            panel.stop()
        release.set()
        until(lambda: not panel.busy)
        if outcome == "stop":
            assert [c for c in calls if c[0] == "send"] == [("send", "B", "reply-B1")]
            assert panel.cases["A1"].phase == panel.cases["A2"].phase == "canceled"
        else:
            until(lambda: panel.cases["A2"].phase == "auto_sent")
            assert panel.connected
            if outcome == "success":
                assert histories["A2"] == [{"role": "user", "content": "A1"},
                                           {"role": "assistant", "content": "reply-A1"}]
            else:
                assert panel.cases["A1"].phase == "failed" and histories["A2"] == []
            panel.add_packet(packet("B2", "B"))
            panel.advance()
            until(lambda: panel.cases["B2"].phase == "auto_sent")
            assert histories["B2"] == [{"role": "user", "content": "B1"},
                                       {"role": "assistant", "content": "reply-B1"}]
        assert errors == []
    finally:
        release.set()
        panel.stop()
        until(lambda: not panel.busy)
        panel.close()
