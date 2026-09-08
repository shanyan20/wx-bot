from dataclasses import replace

import pytest

from wechat_bot.domain import Message
from wechat_bot.policy import decide


def message(**changes):
    return replace(Message("mock", "m1", "friend-demo", "alice", "你好", 100), **changes)


@pytest.mark.parametrize(("changes", "reason"), [
    ({"is_self": True}, "self_message"),
    ({"kind": "image"}, "unsupported_kind"),
    ({"conversation_id": "stranger"}, "not_allowlisted"),
    ({"text": "  "}, "empty_text"),
    ({"created_at": -1000}, "expired_on_receive"),
    ({"created_at": 1000}, "future_timestamp"),
])
def test_filter_reasons(settings, changes, reason):
    result = decide(message(**changes), settings, now=100)
    assert not result.accept
    assert result.reason == reason


def test_group_requires_prefix_or_trusted_mention(settings):
    group = message(chat_type="group", conversation_id="group-demo")
    assert not decide(group, settings, 100).accept
    triggered = replace(group, text="/问 你是谁")
    assert decide(triggered, settings, 100).prompt == "你是谁"
    assert decide(replace(group, mentioned=True), settings, 100).accept


def test_group_sessions_are_isolated():
    first = message(chat_type="group", conversation_id="g")
    assert first.session_id != replace(first, sender_id="bob").session_id
    assert first.session_id != replace(first, chat_type="private").session_id


def test_input_limit(settings):
    assert decide(message(text="x" * 4001), settings, 100).reason == "input_too_long"

