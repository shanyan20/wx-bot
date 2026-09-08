"""命令行组合根：只在这里选择基础设施实现，业务核心不负责创建具体依赖。"""

import argparse
import asyncio
import json
import signal
import sys
import time
import uuid
from pathlib import Path

from wechat_bot.adapters.mock import MockAdapter, append_message
from wechat_bot.config import load_settings
from wechat_bot.domain import Message
from wechat_bot.engine import Engine
from wechat_bot.locking import InstanceLock
from wechat_bot.observability import event, setup_logging
from wechat_bot.services.model import EchoModel, HttpModel
from wechat_bot.storage import Store


def build_adapter(settings):
    if settings.adapter == "mock":
        return MockAdapter(settings.inbox, settings.outbox)
    from wechat_bot.adapters.windows_uia import WindowsUIAAdapter

    return WindowsUIAAdapter(settings.windows)


async def serve(settings, store):
    model = HttpModel(settings) if settings.model == "http" else EchoModel()
    try:
        adapter = build_adapter(settings)
    except BaseException:
        await model.close()
        raise
    engine = Engine(settings, store, adapter, model)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Windows 的 Proactor loop 不实现 add_signal_handler，使用标准 signal 桥接。
    old = {}
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for name in signals:
        old[name] = signal.getsignal(name)
        signal.signal(name, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await engine.run(stop)
    finally:
        for name, handler in old.items():
            signal.signal(name, handler)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="微信 bot：默认 mock 接入 + DeepSeek 真实模型")
    result.add_argument("--config", type=Path, default=Path("config/deepseek.toml"))
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="启动；Ctrl+C 优雅停止")
    sub.add_parser("check", help="校验配置，不连接微信或模型")
    sub.add_parser("status", help="显示队列状态与心跳新鲜度")
    sub.add_parser("jobs", help="显示最近任务，不输出正文")
    for command in ("pause", "resume"):
        child = sub.add_parser(command)
        child.add_argument("--conversation", default="*", help="默认 * 表示全局")
    child = sub.add_parser("resolve", help="人工核对后处置 uncertain；不自动重发")
    child.add_argument("job_id", type=int)
    child.add_argument("--as", dest="state", choices=["sent", "canceled"], required=True)
    child = sub.add_parser("backup", help="在线一致性备份 SQLite")
    child.add_argument("target", type=Path)
    child = sub.add_parser("inject", help="向 mock 追加一条测试消息")
    child.add_argument("text")
    child.add_argument("--conversation", default="friend-demo")
    child.add_argument("--sender", default="user-demo")
    child.add_argument("--group", action="store_true")
    child = sub.add_parser("model-test", help="真实调用配置模型；可传本地图片，不连接微信")
    child.add_argument("--prompt", default="请仅回复：模型连接成功")
    child.add_argument("--image", type=Path, action="append", default=[])
    child = sub.add_parser("chat", help="命令行充当好友，连续与 bot 对话")
    child.add_argument("--echo", action="store_true", help="使用离线回显模型")
    return result


async def model_test(settings, prompt: str, images: list[Path]) -> str:
    if settings.model != "http":
        raise ValueError("model-test 需要 HTTP 模型配置，请使用 config/deepseek.toml")
    model = HttpModel(settings)
    try:
        return await asyncio.wait_for(
            model.reply(prompt, [], images=images), timeout=settings.model_timeout_seconds,
        )
    finally:
        await model.close()


def main() -> int:
    # Windows 管道默认编码随系统区域变化；统一 UTF-8 便于中文日志/脚本消费。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args()
    try:
        settings = load_settings(args.config)
        if args.command == "chat":
            from wechat_bot.console import chat

            asyncio.run(chat(settings, echo=args.echo))
            return 0
        if args.command == "model-test":
            result = asyncio.run(model_test(settings, args.prompt, args.image))
            print(result)
            return 0
        if args.command == "check":
            if settings.adapter == "windows_uia":
                # 构造只校验 profile，不访问桌面；释放尚未启动的线程池。
                adapter = build_adapter(settings)
                adapter.executor.shutdown()
            print(f"配置有效：adapter={settings.adapter}, model={settings.model}")
            print("注意：check 不验证真实客户端兼容性、联网或模型凭证。")
            return 0
        if args.command == "inject":
            if settings.adapter != "mock":
                raise ValueError("inject 仅支持 mock 模式")
            append_message(settings.inbox, Message(
                source="mock", message_id=uuid.uuid4().hex,
                conversation_id=args.conversation, sender_id=args.sender, text=args.text,
                created_at=time.time(), chat_type="group" if args.group else "private",
            ))
            print("测试消息已写入 mock inbox")
            return 0
        if args.command == "run":
            setup_logging(settings.logs)
            with InstanceLock(settings.database.with_suffix(".lock")):
                store = Store(settings.database)
                try:
                    store.recover(time.time())
                    asyncio.run(serve(settings, store))
                finally:
                    store.close()
            return 0
        store = Store(settings.database)
        try:
            if args.command == "status":
                data = store.status(time.time())
                if settings.heartbeat.exists():
                    heartbeat = json.loads(settings.heartbeat.read_text(encoding="utf-8"))
                    data["heartbeat_age_seconds"] = time.time() - heartbeat["timestamp"]
                    data["last_poll_ok"] = heartbeat.get("last_poll_ok")
                    data["poll_failures"] = heartbeat.get("poll_failures")
                else:
                    data["heartbeat_age_seconds"] = None
                data["pause_file"] = settings.pause_file.exists()
                print(json.dumps(data, ensure_ascii=False, indent=2))
            elif args.command == "jobs":
                print(json.dumps(store.recent(), ensure_ascii=False, indent=2))
            elif args.command == "pause":
                store.pause(args.conversation, time.time())
                print("已暂停并取消尚未发送的排队任务；已开始的发送无法撤回。")
            elif args.command == "resume":
                store.resume(args.conversation)
                print("已解除数据库暂停；仍需检查 PAUSE 文件和其他暂停项。旧任务不会重放。")
            elif args.command == "resolve":
                store.resolve(args.job_id, args.state, time.time())
                print("已记录人工处置")
            elif args.command == "backup":
                target = args.target.resolve()
                if target.exists():
                    raise ValueError("备份目标已存在，请使用新的文件名")
                store.backup(target)
                print(f"备份完成：{target}")
        finally:
            store.close()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if args.command == "run":
            event("fatal", error_type=type(exc).__name__)
        # 配置/验证错误可读；运行时网络异常不输出原文以免暴露正文或凭证。
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print(f"启动或操作失败：{detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
