import asyncio
import json
from dataclasses import asdict

import pytest

from wechat_bot.adapters.mock import MockAdapter
from wechat_bot.config import load_settings
from wechat_bot.domain import Message
from wechat_bot.locking import InstanceLock


def test_mock_partial_line_waits_for_completion(settings):
    async def scenario():
        message = Message("mock", "one", "friend-demo", "alice", "hello", 100)
        encoded = json.dumps(asdict(message)).encode()
        settings.inbox.write_bytes(encoded[:20])
        adapter = MockAdapter(settings.inbox, settings.outbox)
        assert await adapter.poll() == []
        await adapter.ack([])
        with settings.inbox.open("ab") as stream:
            stream.write(encoded[20:] + b"\n")
        assert await adapter.poll() == [message]
        # 未 ack 就再次读取，不能丢失消息。
        assert await adapter.poll() == [message]
        await adapter.ack([message])
        assert await adapter.poll() == []
    asyncio.run(scenario())


def test_instance_lock_released_after_exit(tmp_path):
    path = tmp_path / "bot.lock"
    with InstanceLock(path), pytest.raises(RuntimeError), InstanceLock(path):
        pass
    with InstanceLock(path):
        pass


@pytest.mark.parametrize("content", [
    '[app]\nworkers = 0', '[app]\npoll_seconds = nan',
    '[rules]\ngroup_prefix = ""', '[rules]\nprivate_allowlist = "everyone"',
])
def test_invalid_settings_rejected(tmp_path, content):
    path = tmp_path / "bad.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(path)


def test_windows_profile_fails_closed_before_importing_uia():
    import os

    from wechat_bot.adapters.windows_uia import WindowsUIAAdapter

    with pytest.raises(ValueError if os.name == "nt" else RuntimeError):
        WindowsUIAAdapter({"verified": False})

