"""测试 UIA 发送边界的业务行为；替身不证明任何微信客户端控件兼容性。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from wechat_bot.adapters.windows_uia import WindowsUIAAdapter
from wechat_bot.domain import Job, Message, NotSentError, UncertainSendError


def adapter_and_job(draft="", invoke_error=False):
    adapter = WindowsUIAAdapter.__new__(WindowsUIAAdapter)
    adapter.chats = {"friend-demo": {"id": "friend-demo", "input_id": "input",
                                     "send_button_id": "send"}}
    edit = SimpleNamespace(value=draft)
    edit.get_value = lambda: edit.value
    edit.set_edit_text = lambda value: setattr(edit, "value", value)
    calls = []

    def invoke():
        calls.append("send")
        if invoke_error:
            raise RuntimeError("lost window after invoke")

    adapter._window = lambda _: object()
    adapter._one = lambda _, identity: (
        edit if identity == "input" else SimpleNamespace(invoke=invoke)
    )
    message = Message("windows_uia", "inbound", "friend-demo", "alice", "question", 100)
    reply = replace(message, message_id="outbound", sender_id="self", is_self=True, text="answer")
    adapter._snapshot = lambda _: [message, reply] if calls else [message]
    return adapter, Job(1, message, "question", 1, "answer"), calls, edit


def test_existing_draft_prevents_send_and_is_preserved():
    adapter, job, calls, edit = adapter_and_job(draft="human draft")
    with pytest.raises(NotSentError):
        adapter._send(job)
    assert calls == []
    assert edit.value == "human draft"


def test_exception_after_invocation_is_uncertain():
    adapter, job, calls, _ = adapter_and_job(invoke_error=True)
    with pytest.raises(UncertainSendError):
        adapter._send(job)
    assert calls == ["send"]


def test_new_matching_outgoing_row_is_local_send_evidence():
    adapter, job, calls, _ = adapter_and_job()
    receipt = adapter._send(job)
    assert calls == ["send"]
    assert receipt.evidence == "uia_visible:outbound"


def test_unknown_chat_never_touches_desktop():
    adapter, job, calls, edit = adapter_and_job()
    job = replace(job, message=replace(job.message, conversation_id="unknown"))
    with pytest.raises(NotSentError):
        adapter._send(job)
    assert calls == [] and edit.value == ""


def test_input_readback_mismatch_never_invokes_send():
    adapter, job, calls, edit = adapter_and_job()
    edit.set_edit_text = lambda value: setattr(edit, "value", "truncated")
    with pytest.raises(NotSentError):
        adapter._send(job)
    assert calls == []


def test_chat_changes_after_typing_never_invokes_send():
    adapter, job, calls, _ = adapter_and_job()
    windows = iter([object()])
    adapter._window = lambda _: next(windows)
    with pytest.raises(NotSentError):
        adapter._send(job)
    assert calls == []


@pytest.mark.parametrize("self_flag,text,identity", [
    (True, "answer", "inbound"),  # old row is not evidence
    (False, "answer", "new"),  # another sender is not evidence
    (True, "different", "new"),
])
def test_no_new_matching_self_row_times_out_uncertain(monkeypatch, self_flag, text, identity):
    from wechat_bot.adapters import windows_uia
    adapter, job, calls, _ = adapter_and_job()
    candidate = replace(job.message, message_id=identity, text=text, is_self=self_flag)
    adapter._snapshot = lambda _: [candidate] if calls else [job.message]
    clock = iter([0, 1, 6])
    monkeypatch.setattr(windows_uia.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(windows_uia.time, "sleep", lambda _: None)
    with pytest.raises(UncertainSendError):
        adapter._send(job)
    assert calls == ["send"]
