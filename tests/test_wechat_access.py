"""Personal WeChat UIA contract tests; synthetic controls, never a real desktop."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from wechat_bot.adapters.windows_uia import WindowsUIAAdapter
from wechat_bot.engine import Engine
from wechat_bot.services.model import EchoModel


class Control:
    def __init__(self, identity="", text="", children=()):
        self.element_info = SimpleNamespace(automation_id=identity)
        self.text = text
        self.nodes = list(children)

    def window_text(self):
        return self.text

    def descendants(self):
        return self.nodes

    def children(self, **kwargs):
        assert kwargs == {"control_type": "ListItem"}
        return self.nodes

    def verify_actionable(self):
        pass


def row(identity, body="你好", sender="alice", timestamp=None):
    return Control(identity, children=[
        Control("sender", sender), Control("body", body),
        Control("timestamp", timestamp or "2026-09-09T10:00:00+08:00"),
    ])


@pytest.fixture
def uia():
    adapter = WindowsUIAAdapter.__new__(WindowsUIAAdapter)
    chat = dict(id="friend-demo", window_title="test", process_id=123,
                header_id="header", header_text="stable-test-identity", list_id="list",
                sender_id="sender", body_id="body", timestamp_id="timestamp",
                self_sender="self", chat_type="private")
    listing = Control("list")
    header = Control("header", "stable-test-identity")
    window = Control(children=[header, listing])
    adapter.desktop = SimpleNamespace(windows=lambda **kwargs: [window])
    adapter.chats = {chat["id"]: chat}
    adapter.seen, adapter.pending = {}, {}
    return adapter, chat, listing, header


def test_snapshot_preserves_unicode_identity_and_self_flag(uia):
    adapter, chat, listing, _ = uia
    listing.nodes = [row("1", "你好🙂\n第二行"), row("2", sender="self")]
    first, second = adapter._snapshot(chat)
    assert first.text == "你好🙂\n第二行"
    assert json.loads(first.message_id) == ["friend-demo", "1"]
    assert not first.is_self and second.is_self
    assert first.created_at == 1788919200


@pytest.mark.parametrize("rows", [
    [row("")], [row("1"), row("1")],
    [row("1", timestamp="2026-09-09T10:00:00")],
    [row("1", timestamp="昨天")], [row("1", sender="")],
    [Control("system-message")],
])
def test_unreliable_rows_fail_closed(uia, rows):
    adapter, chat, listing, _ = uia
    listing.nodes = rows
    with pytest.raises(ValueError):
        adapter._snapshot(chat)


def test_duplicate_field_rejected(uia):
    adapter, chat, listing, _ = uia
    message = row("1")
    message.nodes.append(Control("sender", "another-user"))
    listing.nodes = [message]
    with pytest.raises(ValueError, match="不唯一"):
        adapter._snapshot(chat)


def test_changed_chat_identity_rejected(uia):
    adapter, chat, _, header = uia
    header.text = "different-chat"
    with pytest.raises(ValueError, match="标题"):
        adapter._snapshot(chat)


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_window_rejected(uia, count):
    adapter, chat, _, _ = uia
    adapter.desktop.windows = lambda **kwargs: [object()] * count
    with pytest.raises(ValueError, match="窗口"):
        adapter._window(chat)


def test_startup_baseline_replay_until_ack_and_identical_text(uia):
    adapter, _, listing, _ = uia
    listing.nodes = [row("old")]
    assert adapter._poll() == []
    listing.nodes += [row("new-1"), row("new-2")]
    batch = adapter._poll()
    assert len(batch) == 2
    assert batch[0].text == batch[1].text
    assert batch[0].message_id != batch[1].message_id
    assert adapter._poll() == batch  # no DB ack: replay
    adapter._ack()
    assert adapter._poll() == []


def test_failed_snapshot_does_not_advance_cursor(uia):
    adapter, _, listing, _ = uia
    assert adapter._poll() == []
    listing.nodes = [row("new"), Control("bad-row")]
    with pytest.raises(ValueError):
        adapter._poll()
    listing.nodes.pop()
    assert len(adapter._poll()) == 1


def test_real_parser_to_engine_sqlite_echo_and_dedup(uia, settings, store):
    """Run real parser/poll/ack + core; only desktop and outbound transport are fake."""
    adapter, _, listing, _ = uia
    sent = []

    async def poll():
        return adapter._poll()

    async def ack(messages):
        adapter._ack()

    async def send(job):
        from wechat_bot.domain import SendReceipt
        sent.append(job.reply)
        return SendReceipt("synthetic-uia")

    async def close():
        pass

    adapter.poll, adapter.ack, adapter.send, adapter.close = poll, ack, send, close

    async def scenario():
        from datetime import UTC, datetime
        engine = Engine(settings, store, adapter, EchoModel())
        try:
            await engine.tick()  # empty baseline
            now = datetime.now(UTC).isoformat()
            listing.nodes = [row("1", timestamp=now), row("2", timestamp=now),
                             row("self", sender="self", timestamp=now)]
            deadline = time.monotonic() + 3
            while store.status(time.time())["counts"].get("sent", 0) != 2:
                assert time.monotonic() < deadline, "integration timeout"
                await engine.tick()
                await asyncio.sleep(0.005)
            # Scroll away and back: persistent dedup must reject old IDs.
            listing.nodes = []
            engine.next_poll_at = 0
            await engine.tick()
            listing.nodes = [row("1", timestamp=now), row("2", timestamp=now)]
            engine.next_poll_at = 0
            await engine.tick()
            assert sent == ["收到：你好", "收到：你好"]
            assert store.status(time.time())["counts"].get("sent") == 2
        finally:
            await engine.close()
    asyncio.run(scenario())
