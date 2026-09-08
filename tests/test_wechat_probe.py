"""The opt-in probe must not disclose chat bodies or call send methods."""

import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wechat_probe", ROOT / "scripts/wechat_probe.py")
probe_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_module)


def test_template_is_blocked_without_desktop_access():
    result = asyncio.run(probe_module.probe(ROOT / "config/windows.example.toml"))
    assert result["status"] == "blocked"
    assert result["live_send_verified"] is False


def test_probe_returns_counts_only_and_closes(monkeypatch, tmp_path):
    closed = []

    class Adapter:
        def __init__(self, profile):
            self.chats = {"private-id": {}}

        def _snapshot(self, chat):
            return ["PRIVATE CHAT BODY"]

        async def _call(self, method, *args):
            return method(*args)

        async def close(self):
            closed.append(True)

    path = tmp_path / "profile.toml"
    path.write_text("[windows]\nverified = true\n", encoding="utf-8")
    monkeypatch.setattr(probe_module, "WindowsUIAAdapter", Adapter)
    result = asyncio.run(probe_module.probe(path))
    assert result["message_counts"] == [1]
    assert "PRIVATE" not in str(result) and "private-id" not in str(result)
    assert closed == [True]
