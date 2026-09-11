import os
import time
import tkinter as tk

import pytest

from wechat_bot.acceptance import Packet
from wechat_bot.control import SessionGate


@pytest.mark.skipif(os.name != "nt" and not os.environ.get("DISPLAY"), reason="No GUI display")
def test_read_model_draft_chain_automatically_but_send_requires_confirmation(tmp_path, monkeypatch):
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
    confirmations = []
    decision = [False]
    def confirm(*args, **kwargs):
        confirmations.append(args)
        return decision[0]
    monkeypatch.setattr(module.messagebox, "askyesno", confirm)
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
        assert case.phase == "draft_review" and calls == ["read", "model", "draft"]
        assert confirmations == [] and errors == []
        assert case.review_policy == "send_only" and not case.a_pass and not case.b_pass
        assert "hello" in panel.tabs["friend"]["texts"][0].get("1.0", "end")
        assert "model reply" in panel.tabs["friend"]["texts"][1].get("1.0", "end")
        panel.advance()
        assert "send" not in calls
        panel.action("friend", "send")  # human declines
        assert case.phase == "draft_review" and "send" not in calls
        decision[0] = True
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


def test_pending_draft_blocks_only_its_chat_and_never_auto_sends():
    from types import SimpleNamespace

    from wechat_bot.acceptance import ReviewCase
    from wechat_bot.acceptance_panel import AcceptancePanel
    first = ReviewCase(Packet("1", "a", "friend", "private", "f", 1, "text", "first"),
                       phase="draft_review")
    second = ReviewCase(Packet("2", "a", "friend", "private", "f", 2, "text", "second"))
    other = ReviewCase(Packet("3", "a", "other", "private", "o", 3, "text", "other"))
    panel = AcceptancePanel.__new__(AcceptancePanel)
    panel.closed = panel.busy = False
    panel.connected = True
    panel.cases = {c.packet.key: c for c in (first, second, other)}
    panel.backend = SimpleNamespace(targets={"friend": {}, "other": {}})
    panel.store = SimpleNamespace(save=lambda c: None)
    panel.display = lambda c: None
    calls = []
    panel.action = lambda *args: calls.append(args)
    panel.advance()
    assert calls == [("other", "model")]
    assert second.phase == "a_review"
    first.phase = "complete"
    panel.advance()
    assert calls[-1] == ("friend", "model")
    assert all(action == "model" for _, action in calls)
