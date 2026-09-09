from dataclasses import replace

import pytest

from wechat_bot.adapters.native_identity import native_message_key
from wechat_bot.domain import Message
from wechat_bot.policy import Decision


def key(**changes):
    fields = dict(account="account", generation="lineage", shard="message_0.db",
                  conversation="wxid_test", table="Msg_test", local_id=1)
    return native_message_key(**(fields | changes))


@pytest.mark.parametrize("changes", [
    {"account": "other"}, {"generation": "rebuilt"}, {"shard": "message_1.db"},
    {"conversation": "other"}, {"table": "Msg_other"}, {"local_id": 2},
])
def test_local_ids_are_scoped(changes):
    assert key() != key(**changes)


@pytest.mark.parametrize("value", [0, -1, True, "1", None])
def test_missing_native_id_never_gets_a_synthetic_fallback(value):
    with pytest.raises(ValueError):
        key(local_id=value)


def test_identical_text_is_distinct_but_same_native_record_replays_once(store):
    message = Message("wechat_db", key(), "wxid_test", "sender", "same text", 100)
    decision = Decision(True, prompt="same text", reason="test")
    assert store.ingest(message, decision, 100)
    assert not store.ingest(message, decision, 101)
    assert store.ingest(replace(message, message_id=key(local_id=2)), decision, 102)


def test_delimiters_do_not_collide():
    assert key(account="a:b", generation="c") != key(account="a", generation="b:c")
