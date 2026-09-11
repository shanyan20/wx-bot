import os
import time
import tkinter as tk

import pytest

from wechat_bot.acceptance import Packet
from wechat_bot.control import SessionGate


@pytest.mark.skipif(os.name != "nt" and not os.environ.get("DISPLAY"), reason="No GUI display")
def test_real_widgets_require_separate_reviews_before_each_effect(tmp_path, monkeypatch):
    from wechat_bot import acceptance_panel as module

    (tmp_path / "config").mkdir()
    (tmp_path / "config/deepseek.toml").write_text('[model]\nprovider="echo"\n')
    calls, errors = [], []
    packet = Packet("native-1", "account", "friend", "private", "friend", 100, "text", "hello")

    class Backend:
        def __init__(self, project):
            self.gate = SessionGate(set())
            self.targets = {}

        def candidates(self):
            return [{"id": "friend", "chat_type": "private", "window_title": "shanyan"}]

        def prepare_connect(self, chats):
            self.targets = {c["id"]: c for c in chats}
            self.gate = SessionGate({("private", "friend")})

        def connect(self):
            self.gate.enable()

        def latest(self, conversation, kind):
            calls.append("read")
            return packet

        def poll(self):
            return []

        def draft(self, conversation, reply):
            self.gate.action(self.targets[conversation], lambda: calls.append("draft"))
            return "readback verified"

        def send(self, conversation, reply):
            self.gate.action(self.targets[conversation], lambda: calls.append("send"))
            return "native_db_outgoing:synthetic"

        def stop(self):
            self.gate.request_stop()

        def close(self):
            self.stop()

    class Model:
        def __init__(self, settings):
            pass

        async def reply(self, prompt, history, images=None):
            calls.append("model")
            assert prompt == "hello" and history == [] and images is None
            return "model reply"

        async def close(self):
            pass

    monkeypatch.setattr(module, "NativeReview", Backend)
    monkeypatch.setattr(module, "HttpModel", Model)
    monkeypatch.setattr(module.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(module.messagebox, "showerror", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(module.AcceptancePanel, "initialize",
                        lambda self: (Backend(self.project), Backend(self.project).candidates()))
    root = tk.Tk()
    root.withdraw()
    panel = module.AcceptancePanel(root, tmp_path)

    def settle():
        until = time.monotonic() + 3
        while panel.busy and time.monotonic() < until:
            root.update()
            time.sleep(0.01)
        assert not panel.busy

    try:
        settle()
        assert not panel.connected and calls == []
        panel.connect()
        settle()
        assert panel.connected and calls == []
        panel.latest("friend", 1)
        settle()
        case = panel.active("friend")
        assert case.phase == "a_review" and calls == ["read"]
        assert "hello" in panel.tabs["friend"]["texts"][0].get("1.0", "end")
        panel.action("friend", "model")  # denied before A verdict
        assert calls == ["read"] and len(errors) == 1
        panel.action("friend", "approve_a")
        assert calls == ["read"]
        panel.action("friend", "model")
        settle()
        assert case.phase == "b_review" and calls == ["read", "model"]
        assert "model reply" in panel.tabs["friend"]["texts"][1].get("1.0", "end")
        panel.action("friend", "approve_b")
        panel.action("friend", "draft")
        settle()
        assert case.phase == "draft_review" and "send" not in calls
        panel.action("friend", "send")
        settle()
        assert case.phase == "c_review" and not case.c_pass
        panel.action("friend", "approve_c")
        assert case.phase == "complete" and case.c_pass
        assert calls == ["read", "model", "draft", "send"]
        panel.stop()
        panel.action("friend", "send")
        assert calls.count("send") == 1
    finally:
        settle()
        panel.close()
