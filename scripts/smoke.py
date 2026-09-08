"""独立进程 CLI 验证：临时文件、离线 mock 收发、管理命令与优雅退出。

运行 conda run --prefix E:/project/bot/.conda python scripts/smoke.py。
不会创建真实微信连接或修改正式数据。
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONUTF8": "1"}
    with tempfile.TemporaryDirectory(prefix="wechat-bot-smoke-") as directory:
        temp = Path(directory)
        configuration = temp / "smoke.toml"
        configuration.write_text('''
[app]
database = "bot.sqlite3"
logs = "bot.log"
heartbeat = "heartbeat.json"
pause_file = "PAUSE"
poll_seconds = 0.05
send_interval_seconds = 0.05
[adapter]
type = "mock"
inbox = "inbox.jsonl"
outbox = "outbox.jsonl"
[rules]
private_allowlist = ["friend-demo"]
group_allowlist = ["group-demo"]
group_prefix = "/问 "
[model]
provider = "echo"
''', encoding="utf-8")
        command = [sys.executable, "-m", "wechat_bot", "--config", str(configuration)]

        def cli(*arguments):
            return subprocess.run(
                [*command, *arguments], cwd=root, env=env, check=True,
                capture_output=True, text=True, encoding="utf-8", timeout=15,
            ).stdout

        cli("check")
        cli("inject", "你好")
        cli("inject", "/问 群测试", "--group", "--conversation", "group-demo")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            [*command, "run"], cwd=root, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=flags,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                status = json.loads(cli("status"))
                if status["counts"].get("sent") == 2:
                    break
                if process.poll() is not None:
                    raise AssertionError("bot exited before replies")
                time.sleep(0.1)
            else:
                raise AssertionError("timed out waiting for replies")
            records = [json.loads(line) for line in
                       (temp / "outbox.jsonl").read_text(encoding="utf-8").splitlines()]
            assert {r["reply"] for r in records} == {"收到：你好", "收到：群测试"}
            cli("pause", "--conversation", "friend-demo")
            assert "friend-demo" in json.loads(cli("status"))["paused"]
            cli("resume", "--conversation", "friend-demo")
            cli("backup", str(temp / "backup.sqlite3"))
            assert (temp / "backup.sqlite3").exists()
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            _, errors = process.communicate(timeout=10)
            assert process.returncode == 0, (
                f"bot exit code: {process.returncode}; " + errors.decode("utf-8", errors="replace")
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stderr is not None:
                process.stderr.close()
        print("PASS: CLI check / private+group replies / pause+resume / backup / graceful stop")


if __name__ == "__main__":
    main()
