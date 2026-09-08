"""测试只使用临时目录和假适配器，不接触真实微信、不请求付费模型。"""

from dataclasses import replace
from pathlib import Path

import pytest

from wechat_bot.config import load_settings
from wechat_bot.storage import Store


@pytest.fixture
def settings(tmp_path):
    base = load_settings(Path(__file__).parents[1] / "config/example.toml")
    return replace(
        base, database=tmp_path / "bot.sqlite3", logs=tmp_path / "bot.log",
        heartbeat=tmp_path / "heartbeat.json", pause_file=tmp_path / "PAUSE",
        inbox=tmp_path / "inbox.jsonl", outbox=tmp_path / "outbox.jsonl",
        poll_seconds=0.01, send_interval_seconds=0.01, retry_base_seconds=0.01,
    )


@pytest.fixture
def store(settings):
    instance = Store(settings.database)
    yield instance
    instance.close()

