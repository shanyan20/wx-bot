"""TOML 配置加载与边界校验。相对路径统一相对配置文件，避免 cwd 改变数据位置。"""

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Settings:
    adapter: str
    database: Path
    logs: Path
    heartbeat: Path
    pause_file: Path
    inbox: Path
    outbox: Path
    poll_seconds: float
    workers: int
    message_ttl_seconds: float
    max_attempts: int
    retry_base_seconds: float
    send_interval_seconds: float
    max_input_chars: int
    max_reply_chars: int
    context_turns: int
    private_allowlist: frozenset[str]
    group_allowlist: frozenset[str]
    group_prefix: str
    model: str
    model_name: str
    base_url: str
    api_key_env: str
    model_timeout_seconds: float
    fallback: str
    system_prompt: str
    windows: dict[str, Any]
    api_key_file: Path | None = None
    model_max_tokens: int = 1024


def load_settings(path: Path) -> Settings:
    path = path.resolve()
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    app, adapter, rules, model = (
        raw.get("app", {}), raw.get("adapter", {}), raw.get("rules", {}), raw.get("model", {})
    )

    def local(value: str) -> Path:
        return (path.parent / value).resolve()

    def allowlist(name: str) -> frozenset[str]:
        values = rules.get(name, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            raise ValueError(f"rules.{name} 必须是非空字符串数组")
        return frozenset(values)

    # TODO(T10)：并发、TTL 和发送间隔是初始默认值，需根据真实群规模验证。
    result = Settings(
        adapter=adapter.get("type", "mock"),
        database=local(app.get("database", "../data/bot.sqlite3")),
        logs=local(app.get("logs", "../data/logs/bot.log")),
        heartbeat=local(app.get("heartbeat", "../data/heartbeat.json")),
        pause_file=local(app.get("pause_file", "../data/PAUSE")),
        inbox=local(adapter.get("inbox", "../data/inbox.jsonl")),
        outbox=local(adapter.get("outbox", "../data/outbox.jsonl")),
        poll_seconds=app.get("poll_seconds", 1.0),
        workers=app.get("workers", 2),
        message_ttl_seconds=app.get("message_ttl_seconds", 300.0),
        max_attempts=app.get("max_attempts", 3),
        retry_base_seconds=app.get("retry_base_seconds", 2.0),
        send_interval_seconds=app.get("send_interval_seconds", 2.0),
        max_input_chars=rules.get("max_input_chars", 4000),
        max_reply_chars=rules.get("max_reply_chars", 1500),
        context_turns=rules.get("context_turns", 6),
        private_allowlist=allowlist("private_allowlist"),
        group_allowlist=allowlist("group_allowlist"),
        group_prefix=rules.get("group_prefix", "/问 "),
        model=model.get("provider", "echo"),
        model_name=model.get("name", ""),
        base_url=model.get("base_url", ""),
        api_key_env=model.get("api_key_env", "BOT_API_KEY"),
        model_timeout_seconds=model.get("timeout_seconds", 30.0),
        fallback=model.get("fallback", "暂时无法生成回复，请稍后再试。"),
        system_prompt=model.get("system_prompt", "你是简洁、友善的聊天助手。未知信息请明确说明。"),
        windows=raw.get("windows", {}),
        api_key_file=local(model["api_key_file"]) if model.get("api_key_file") else None,
        model_max_tokens=model.get("max_tokens", 1024),
    )
    if result.adapter not in {"mock", "windows_uia"} or result.model not in {"echo", "http"}:
        raise ValueError("不支持的 adapter.type 或 model.provider")
    for name in (
        "poll_seconds", "message_ttl_seconds", "retry_base_seconds", "send_interval_seconds",
        "model_timeout_seconds", "workers", "max_attempts", "max_input_chars", "max_reply_chars",
        "model_max_tokens",
    ):
        value = getattr(result, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} 必须为有限正数")
    for name in ("workers", "max_attempts", "max_input_chars", "max_reply_chars", "context_turns",
                 "model_max_tokens"):
        value = getattr(result, name)
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} 必须为非负整数")
    if not isinstance(result.group_prefix, str) or not result.group_prefix.strip():
        raise ValueError("group_prefix 不能为空，防止群内所有消息触发回复")
    for name in ("fallback", "system_prompt", "api_key_env"):
        if not isinstance(getattr(result, name), str) or not getattr(result, name).strip():
            raise ValueError(f"{name} 必须是非空字符串")
    paths = [result.database, result.logs, result.heartbeat, result.pause_file,
             result.inbox, result.outbox, result.database.with_suffix(".lock")]
    if len(set(paths)) != len(paths):
        raise ValueError("数据库、日志、心跳、暂停文件、收发文件和实例锁路径不得相同")
    if result.model == "http":
        from urllib.parse import urlparse

        url = urlparse(result.base_url)
        if (url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password
                or url.query or url.fragment):
            raise ValueError("model.base_url 必须为不含凭证的 HTTP(S) URL")
        if not isinstance(result.model_name, str) or not result.model_name.strip():
            raise ValueError("HTTP 模型必须设置 name 和 api_key_env")
    return result
