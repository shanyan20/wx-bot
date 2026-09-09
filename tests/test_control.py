import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from wechat_bot.control import (
    ControlledUIAAdapter,
    Controller,
    SessionGate,
    load_selection,
    save_selection,
    select_settings,
)
from wechat_bot.domain import NotSentError


def test_default_off_blocks_writes_but_allows_scoped_inspection():
    gate = SessionGate({("private", "test")})
    chat = {"chat_type": "private", "id": "test"}
    gate.read(chat)
    calls = []
    with pytest.raises(NotSentError):
        gate.action(chat, lambda: calls.append(True))
    assert calls == []


@pytest.mark.parametrize("chat", [
    {"chat_type": "private", "id": "other"},
    {"chat_type": "group", "id": "test"},
])
def test_whitelist_matches_type_and_identity(chat):
    gate = SessionGate({("private", "test")})
    gate.enable()
    with pytest.raises(NotSentError):
        gate.read(chat)
    with pytest.raises(NotSentError):
        gate.action(chat, lambda: pytest.fail("must never write"))


def test_stop_rejects_queued_actions_and_cannot_reenable_old_session():
    gate = SessionGate({("private", "test")})
    gate.enable()
    chat = {"chat_type": "private", "id": "test"}
    assert gate.action(chat, lambda: 42) == 42
    gate.request_stop()
    with pytest.raises(NotSentError):
        gate.action(chat, lambda: pytest.fail("late write"))
    gate.stop()
    with pytest.raises(ValueError):
        gate.enable()


def test_stop_barrier_drains_inflight_action_without_blocking_request():
    gate = SessionGate({("private", "test")})
    gate.enable()
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def action():
        entered.set()
        assert release.wait(2)

    worker = threading.Thread(target=lambda: gate.action(
        {"chat_type": "private", "id": "test"}, action))
    stopper = threading.Thread(target=lambda: (gate.stop(), stopped.set()))
    worker.start()
    try:
        assert entered.wait(1)
        gate.request_stop()
        stopper.start()
        assert not stopped.wait(0.02)
    finally:
        release.set()
        worker.join(2)
        stopper.join(2)
    assert stopped.is_set() and not gate.enabled


def base_settings(settings):
    return replace(settings, adapter="windows_uia", windows={"verified": True, "chats": [
        {"id": "friend", "chat_type": "private"},
        {"id": "group", "chat_type": "group"},
        {"id": "unrelated", "chat_type": "private"},
    ]})


def test_selection_filters_before_adapter_reads_and_isolates_run_data(settings, tmp_path):
    base = base_settings(settings)
    selected = select_settings(base, {("private", "friend"), ("group", "group")}, tmp_path)
    assert [chat["id"] for chat in selected.windows["chats"]] == ["friend", "group"]
    assert selected.private_allowlist == {"friend"}
    assert selected.group_allowlist == {"group"}
    assert len(base.windows["chats"]) == 3
    assert selected.database == tmp_path / "bot.sqlite3"
    assert selected.model == "echo"


@pytest.mark.parametrize("targets", [set(), {("private", "unknown")}, {("group", "friend")}])
def test_invalid_whitelist_fails_before_desktop(settings, tmp_path, targets):
    with pytest.raises(ValueError):
        select_settings(base_settings(settings), targets, tmp_path)


def test_unverified_selected_binding_cannot_inherit_global_verification(settings, tmp_path):
    base = base_settings(settings)
    base.windows["chats"][0]["verified"] = False
    selected = select_settings(base, {("private", "friend")}, tmp_path)
    assert selected.windows["verified"] is False


def test_whitelist_persistence_never_saves_enabled_state(tmp_path):
    path = tmp_path / "allowlist.json"
    targets = {("private", "test"), ("group", "test-group")}
    save_selection(path, targets)
    assert load_selection(path) == targets
    assert "enabled" not in path.read_text()
    assert not SessionGate(load_selection(path)).enabled


def test_corrupt_whitelist_fails_closed(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text('["test"]')
    with pytest.raises(ValueError):
        load_selection(path)


def test_controlled_adapter_rejects_unlisted_window_before_desktop(monkeypatch):
    from wechat_bot.adapters.windows_uia import WindowsUIAAdapter
    adapter = ControlledUIAAdapter.__new__(ControlledUIAAdapter)
    adapter.gate = SessionGate({("private", "test")})
    monkeypatch.setattr(WindowsUIAAdapter, "_window", lambda *_: pytest.fail("desktop accessed"))
    with pytest.raises(NotSentError):
        adapter._window({"chat_type": "private", "id": "unrelated"})


def test_window_recreation_requires_new_session(monkeypatch):
    from wechat_bot.adapters.windows_uia import WindowsUIAAdapter
    adapter = ControlledUIAAdapter.__new__(ControlledUIAAdapter)
    adapter.gate = SessionGate({("private", "test")})
    adapter.handles = {}
    windows = iter([SimpleNamespace(handle=11), SimpleNamespace(handle=12)])
    monkeypatch.setattr(WindowsUIAAdapter, "_window", lambda *_: next(windows))
    chat = {"chat_type": "private", "id": "test"}
    assert adapter._window(chat).handle == 11
    with pytest.raises(ValueError, match="重建"):
        adapter._window(chat)


def test_stop_between_typing_and_invoke_prevents_send():
    from test_windows_contract import adapter_and_job
    adapter, job, calls, edit = adapter_and_job()
    gate = SessionGate({("private", "friend-demo")})
    gate.enable()
    adapter.chats["friend-demo"]["chat_type"] = "private"
    adapter._write = lambda chat, action: gate.action(chat, action)

    def type_and_stop(text):
        edit.value = text
        gate.request_stop()
    edit.set_edit_text = type_and_stop
    from wechat_bot.domain import UncertainSendError
    with pytest.raises(UncertainSendError):
        adapter._send(job)
    assert calls == []


def test_controller_readonly_never_constructs_model_or_engine(monkeypatch, settings, tmp_path):
    from wechat_bot import control
    calls = []

    class ReadOnlyAdapter:
        def __init__(self, profile, gate):
            assert not gate.enabled

        def _preflight(self):
            calls.append("inspect")

        async def _call(self, callback):
            return callback()

        async def close(self):
            calls.append("close")

    monkeypatch.setattr(control, "ControlledUIAAdapter", ReadOnlyAdapter)
    monkeypatch.setattr(control, "EchoModel", lambda: pytest.fail("model called"))
    controller = Controller(settings, tmp_path, lambda *_: None)
    gate = SessionGate({("private", "friend")})
    asyncio.run(controller._run(settings, gate, True))
    assert calls == ["inspect", "close"] and not gate.enabled
