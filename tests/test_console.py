"""交互好友模拟测试：使用临时目录/替身模型，绝不请求真实 API。"""

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from wechat_bot.console import ConsoleSession, safe_display
from wechat_bot.domain import ModelError
from wechat_bot.services.model import EchoModel


def test_console_uses_history_and_reset_starts_new_session(settings, tmp_path):
    class RecordingModel(EchoModel):
        def __init__(self):
            self.histories = []

        async def reply(self, prompt, history):
            self.histories.append(history)
            return f"answer:{prompt}"

    async def scenario():
        model = RecordingModel()
        session = ConsoleSession(settings, tmp_path / "console", model)
        try:
            assert (await session.ask("first"))["reply"] == "answer:first"
            assert (await session.ask("second"))["state"] == "sent"
            assert model.histories[1] == [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer:first"},
            ]
            session.reset()
            await session.ask("new conversation")
            assert model.histories[2] == []
            assert not settings.database.exists()  # 原配置数据库未被打开。
        finally:
            await session.close()
    asyncio.run(scenario())


def test_console_reports_fallback_and_rejected_input(settings, tmp_path):
    class BrokenModel(EchoModel):
        async def reply(self, prompt, history):
            raise ModelError("test failure")

    async def scenario():
        session = ConsoleSession(
            replace(settings, max_attempts=1), tmp_path / "console", BrokenModel()
        )
        try:
            result = await session.ask("hello")
            assert result["reason"] == "model_fallback"
            assert result["reply"] == settings.fallback
            rejected = await session.ask("x" * (settings.max_input_chars + 1))
            assert rejected["state"] == "ignored"
            assert rejected["reason"] == "input_too_long"
        finally:
            await session.close()
    asyncio.run(scenario())


def test_console_script_accepts_lines_commands_and_exit(tmp_path):
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [sys.executable, "scripts/chat.py", "--echo", "--session-root", str(tmp_path / "sessions")],
        input="\n/help\n你好\n/reset\n第二句\n/quit\n", text=True, encoding="utf-8",
        capture_output=True, cwd=root, env={**os.environ, "PYTHONUTF8": "1"}, timeout=15,
    )
    assert process.returncode == 0, process.stderr
    assert "bot  > 收到：你好" in process.stdout
    assert "bot  > 收到：第二句" in process.stdout
    assert "已开始新对话" in process.stdout
    assert "对话已结束" in process.stdout


def test_console_filters_terminal_escape_characters():
    assert safe_display("hello\x1b\x00\nworld") == "hello\nworld"


@pytest.mark.parametrize("custom_config", [False, True], ids=["project-default", "explicit-config"])
def test_console_script_without_install_from_another_directory(tmp_path, custom_config):
    """禁用 site 和 PYTHONPATH，防止父环境的可编辑安装掩盖脚本导入缺陷。"""
    root = Path(__file__).resolve().parents[1]
    args = [sys.executable, "-I", "-S", str(root / "scripts/chat.py"), "--echo",
            "--session-root", str(tmp_path / "sessions")]
    if custom_config:
        # 用户显式提供的相对配置仍相对当前目录，不能被脚本改写为项目路径。
        (tmp_path / "custom.toml").write_text('[model]\nprovider = "echo"\n', encoding="utf-8")
        args.extend(["--config", "custom.toml"])
    process = subprocess.run(
        args, input="源码启动成功\n/quit\n", text=True, encoding="utf-8",
        capture_output=True, cwd=tmp_path, timeout=15,
    )
    assert process.returncode == 0, process.stderr
    assert "bot  > 收到：源码启动成功" in process.stdout
    assert "对话已结束" in process.stdout
