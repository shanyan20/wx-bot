import asyncio
import json
import time
from dataclasses import replace

from wechat_bot.adapters.mock import MockAdapter, append_message
from wechat_bot.domain import Message, ModelError, NotSentError, SendReceipt, UncertainSendError
from wechat_bot.engine import Engine
from wechat_bot.policy import Decision
from wechat_bot.services.model import EchoModel


class FakeAdapter:
    def __init__(self, error=None):
        self.error = error
        self.send_calls = 0

    async def poll(self):
        return []

    async def ack(self, messages):
        pass

    async def send(self, job):
        self.send_calls += 1
        if self.error:
            raise self.error
        return SendReceipt("fake")

    async def close(self):
        pass


async def pump(engine, predicate):
    for _ in range(100):
        await engine.tick()
        await asyncio.sleep(0.005)
        if predicate():
            return
    raise AssertionError("engine did not reach expected state")


def test_mock_end_to_end_and_restart_no_duplicate(settings, store):
    async def scenario():
        message = Message("mock", "same", "friend-demo", "alice", "你好", time.time())
        append_message(settings.inbox, message)
        append_message(settings.inbox, message)
        engine = Engine(settings, store, MockAdapter(settings.inbox, settings.outbox), EchoModel())
        try:
            await pump(engine, lambda: store.status(time.time())["counts"].get("sent") == 1)
        finally:
            await engine.close()
        restarted = Engine(
            settings, store, MockAdapter(settings.inbox, settings.outbox), EchoModel()
        )
        try:
            for _ in range(5):
                await restarted.tick()
                await asyncio.sleep(0)
        finally:
            await restarted.close()
        lines = settings.outbox.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["reply"] == "收到：你好"
    asyncio.run(scenario())


def test_uncertain_is_never_automatically_retried(settings, store):
    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", time.time())
        store.ingest(message, Decision(True, "hello"), time.time())
        adapter = FakeAdapter(UncertainSendError())
        engine = Engine(settings, store, adapter, EchoModel())
        try:
            await pump(engine, lambda: store.status(time.time())["counts"].get("uncertain") == 1)
            for _ in range(10):
                await engine.tick()
                await asyncio.sleep(0.005)
            assert adapter.send_calls == 1
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_not_sent_retries_are_bounded(settings, store):
    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", time.time())
        store.ingest(message, Decision(True, "hello"), time.time())
        adapter = FakeAdapter(NotSentError())
        engine = Engine(settings, store, adapter, EchoModel())
        try:
            await pump(engine, lambda: store.status(time.time())["counts"].get("failed") == 1)
            assert adapter.send_calls == settings.max_attempts
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_model_failure_falls_back(settings, store):
    class BrokenModel(EchoModel):
        async def reply(self, prompt, history):
            raise ModelError("test")

    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", time.time())
        store.ingest(message, Decision(True, "hello"), time.time())
        engine = Engine(replace(settings, max_attempts=1), store, FakeAdapter(), BrokenModel())
        try:
            await pump(engine, lambda: store.status(time.time())["counts"].get("sent") == 1)
            history = store.history(message.session_id, 1)
            assert history[-1]["content"] == settings.fallback
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_pause_file_cancels_queued_reply(settings, store):
    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", time.time())
        store.ingest(message, Decision(True, "hello"), time.time())
        job = store.claim(time.time(), 3)
        store.ready(job.id, "answer", time.time())
        settings.pause_file.touch()
        adapter = FakeAdapter()
        engine = Engine(settings, store, adapter, EchoModel())
        try:
            await engine.tick()
            await asyncio.sleep(0)
            assert adapter.send_calls == 0
            assert store.status(time.time())["counts"] == {"canceled": 1}
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_removed_allowlist_prevents_reply_after_restart(settings, store):
    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", time.time())
        store.ingest(message, Decision(True, "hello"), time.time())
        job = store.claim(time.time(), 3)
        store.ready(job.id, "answer from old config", time.time())
        adapter = FakeAdapter()
        engine = Engine(
            replace(settings, private_allowlist=frozenset()), store, adapter, EchoModel()
        )
        try:
            await pump(engine, lambda: store.status(time.time())["counts"].get("canceled") == 1)
            assert adapter.send_calls == 0
            assert store.recent()[0]["reason"] == "not_allowlisted"
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_model_total_timeout_falls_back(settings, store):
    class SlowModel(EchoModel):
        async def reply(self, prompt, history):
            await asyncio.sleep(10)
            return "too late"

    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", time.time())
        store.ingest(message, Decision(True, "hello"), time.time())
        engine = Engine(
            replace(settings, model_timeout_seconds=0.01, max_attempts=1),
            store, FakeAdapter(), SlowModel(),
        )
        try:
            await pump(engine, lambda: store.status(time.time())["counts"].get("sent") == 1)
            assert store.history(message.session_id, 1)[-1]["content"] == settings.fallback
        finally:
            await engine.close()
    asyncio.run(scenario())


def test_poll_failure_holds_outbound_queue(settings, store):
    class OfflineAdapter(FakeAdapter):
        async def poll(self):
            raise RuntimeError("offline")

    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", time.time())
        store.ingest(message, Decision(True, "hello"), time.time())
        job = store.claim(time.time(), 3)
        store.ready(job.id, "answer", time.time())
        adapter = OfflineAdapter()
        engine = Engine(settings, store, adapter, EchoModel())
        try:
            await engine.tick()
            await asyncio.sleep(0)
            assert adapter.send_calls == 0
            assert store.status(time.time())["counts"] == {"ready": 1}
            heartbeat = json.loads(settings.heartbeat.read_text(encoding="utf-8"))
            assert heartbeat["poll_failures"] == 1
        finally:
            await engine.close()
    asyncio.run(scenario())
