from types import SimpleNamespace

import pytest

from wechat_bot.adapters.native_review import NativeReview, text_content
from wechat_bot.control import SessionGate
from wechat_bot.domain import NotSentError


def backend():
    result = NativeReview.__new__(NativeReview)
    chat = {"id": "allowed", "chat_type": "private", "input_id": "input"}
    result.gate = SessionGate({("private", "allowed")})
    result.gate.enable()
    result.targets = {"allowed": chat}
    result.stopped = False
    result.window = lambda c: result.gate.read(c)
    return result


def test_stop_before_queued_connect_cannot_reenable():
    b = backend()
    b.read_rows = lambda c: []
    b.prepare_connect(list(b.targets.values()))
    b.stop()
    with pytest.raises(NotSentError):
        b.connect()
    assert b.gate.stopping.is_set() and not b.gate.enabled


def test_non_allowlisted_target_does_not_read_or_write():
    b = backend()
    for method, args in ((b.latest, ("other",)), (b.draft, ("other", "text")),
                         (b.send, ("other", "text"))):
        with pytest.raises(KeyError):
            method(*args)


def test_draft_preserves_existing_input_and_stop_denies_writes():
    b = backend()
    calls = []
    b.uia = SimpleNamespace(_one=lambda *args: SimpleNamespace(
        get_value=lambda: "human draft", set_edit_text=lambda text: calls.append(text)))
    with pytest.raises(ValueError, match="草稿"):
        b.draft("allowed", "model reply")
    b.stop()
    with pytest.raises(NotSentError):
        b.draft("allowed", "model reply")
    assert calls == []


def test_text_and_zstd_content():
    import zstandard
    assert text_content("hello") == "hello"
    assert text_content(zstandard.ZstdCompressor().compress("你好".encode())) == "你好"


def test_poll_baseline_and_self_messages_do_not_trigger():
    b = backend()
    b.self_id = "me"
    b.cursors = {"allowed": {"old"}}
    rows = [{"key": "old", "sender": "friend"}, {"key": "self", "sender": "me"},
            {"key": "new", "sender": "friend"}]
    b.read_rows = lambda c: rows
    b.packet = lambda c, r: r["key"]
    assert b.poll() == ["new"]
    assert b.poll() == []
    b.read_rows = lambda c: [{"key": "missing-continuity", "sender": "friend"}]
    with pytest.raises(ValueError, match="连续性"):
        b.poll()


def test_second_chat_snapshot_conflict_does_not_consume_first_chat_messages():
    from wechat_bot.adapters.native_review import SnapshotChangedError

    b = backend()
    b.targets["second"] = {"id": "second", "chat_type": "private"}
    b.cursors = {key: {"old-" + key} for key in b.targets}
    b.self_id = "me"
    conflict = [True]

    def read(chat):
        if chat["id"] == "second" and conflict[0]:
            raise SnapshotChangedError("concurrent write")
        return [{"key": prefix + chat["id"], "sender": "friend"}
                for prefix in ("old-", "new-")]

    b.read_rows = read
    b.packet = lambda c, r: r["key"]
    with pytest.raises(SnapshotChangedError):
        b.poll()
    assert b.cursors == {key: {"old-" + key} for key in b.targets}
    conflict[0] = False
    assert b.poll() == ["new-allowed", "new-second"]
    assert b.poll() == []


def test_send_uses_one_targeted_click_and_refreshes_transient_snapshot(monkeypatch):
    from wechat_bot.adapters import native_review as module
    b = backend()
    b.cache, b.self_id = {}, "me"
    b.targets["allowed"]["process_id"] = 123
    calls = []
    b.uia = SimpleNamespace(
        _one=lambda *args: SimpleNamespace(get_value=lambda: "reply\r\nline"),
        _send_button=lambda *args, **kwargs: SimpleNamespace(
            invoke=lambda: pytest.fail("old Invoke route must not run")))
    snapshots = iter([[], module.SnapshotChangedError("concurrent write"), [
        {"key": "new", "sender": "me", "kind": 1,
         "content": "reply\nline", "compressed": None}]])
    def read(chat):
        value = next(snapshots)
        if isinstance(value, Exception):
            raise value
        return value
    b.read_rows = read
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "click_send_button", lambda *args:
                        (calls.append(args) or {"method": "hwnd_mouse_pair"}))
    result = b.send("allowed", "reply\r\nline")
    assert "native_db_outgoing:new" in result
    assert len(calls) == 1 and calls[0][2] == 123


def test_changed_draft_blocks_new_mouse_route(monkeypatch):
    from wechat_bot.adapters import native_review as module
    b = backend()
    b.cache = {}
    b.read_rows = lambda c: []
    b.uia = SimpleNamespace(_one=lambda *args: SimpleNamespace(get_value=lambda: "human draft"))
    monkeypatch.setattr(module, "click_send_button", lambda *args: pytest.fail("must not click"))
    with pytest.raises(ValueError, match="草稿改变"):
        b.send("allowed", "reply")
