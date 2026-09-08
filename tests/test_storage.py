from dataclasses import replace

from wechat_bot.domain import Message
from wechat_bot.policy import Decision
from wechat_bot.storage import Store


def enqueue(store, identity="one", conversation="friend-demo", source="mock", now=100):
    msg = Message(source, identity, conversation, "alice", "hello", now)
    store.ingest(msg, Decision(True, "hello"), now)
    return msg


def test_dedup_is_identity_based_not_text_based(store):
    msg = enqueue(store)
    assert not store.ingest(msg, Decision(True, "hello"), 100)
    assert store.ingest(replace(msg, message_id="two"), Decision(True, "hello"), 100)
    assert store.ingest(replace(msg, source="other"), Decision(True, "hello"), 100)
    assert store.status(100)["counts"] == {"pending": 3}


def test_same_conversation_serial_other_conversation_progresses(store):
    enqueue(store, "one")
    enqueue(store, "two")
    enqueue(store, "three", "another")
    first = store.claim(100, 3)
    other = store.claim(100, 3)
    assert first.message.message_id == "one"
    assert other.message.message_id == "three"
    assert store.claim(100, 3) is None
    store.ready(first.id, "answer", 100)
    sending = store.claim_send(100, 1)
    store.sent(sending.id, "evidence", 100)
    assert store.claim(100, 3).message.message_id == "two"


def test_restart_preserves_uncertain_send_and_blocks_following(store, settings):
    enqueue(store, "one")
    enqueue(store, "two")
    job = store.claim(100, 3)
    store.ready(job.id, "answer", 100)
    store.claim_send(100, 1)
    # 新连接模拟崩溃重启；测试本身不再调度旧连接。
    restarted = Store(settings.database)
    try:
        restarted.recover(101)
        assert restarted.status(101)["counts"] == {"pending": 1, "uncertain": 1}
        assert restarted.claim_send(102, 1) is None
        assert restarted.claim(102, 3) is None
        restarted.resolve(job.id, "sent", 103)
        assert restarted.claim(103, 3).message.message_id == "two"
    finally:
        restarted.close()


def test_pause_during_generation_cannot_resurrect_reply(store):
    enqueue(store)
    job = store.claim(100, 3)
    store.pause("friend-demo", 101)
    store.ready(job.id, "late reply", 102)
    assert store.status(102)["counts"] == {"canceled": 1}
    assert store.claim_send(102, 1) is None
    store.resume("friend-demo")
    assert store.claim(103, 3) is None


def test_expiry_and_retry_bounds(store):
    enqueue(store)
    job = store.claim(100, 3)
    store.ready(job.id, "answer", 100)
    store.expire(500, 300)
    assert store.claim_send(500, 1) is None
    assert store.status(500)["counts"] == {"expired": 1}
    enqueue(store, "two", now=500)
    store.claim(500, 1)
    store.recover(501)
    assert store.claim(501, 1) is None
    assert store.status(501)["counts"]["failed"] == 1


def test_persistent_send_rate_limit(store, settings):
    enqueue(store)
    job = store.claim(100, 3)
    store.ready(job.id, "answer", 100)
    store.claim_send(100, 10)
    store.sent(job.id, "ok", 100)
    enqueue(store, "two")
    job2 = store.claim(101, 3)
    store.ready(job2.id, "answer", 101)
    assert store.claim_send(101, 10) is None
    assert store.claim_send(110, 10).id == job2.id


def test_only_confirmed_history_used_and_backup_contains_wal(store, settings, tmp_path):
    msg = enqueue(store)
    job = store.claim(100, 3)
    store.ready(job.id, "answer", 100)
    assert store.history(msg.session_id, 6) == []
    store.claim_send(100, 1)
    store.sent(job.id, "ok", 100)
    assert store.history(msg.session_id, 6) == [
        {"role": "user", "content": "hello"}, {"role": "assistant", "content": "answer"},
    ]
    target = tmp_path / "backup.sqlite3"
    store.backup(target)
    restored = Store(target)
    try:
        assert restored.status(100)["counts"] == {"sent": 1}
    finally:
        restored.close()

