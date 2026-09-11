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
