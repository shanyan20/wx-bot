"""直接运行源码中的交互入口，不要求先执行 pip install -e .。

Python 直接执行 scripts/chat.py 时，只自动把 scripts 加入模块搜索路径，
不会自动发现同级 src。这里只为当前进程添加本项目 src，不修改全局 PYTHONPATH。
"""

import sys
from pathlib import Path


def run() -> int:
    # 直接执行脚本可能误用系统旧 Python；在导入项目的新语法前给出可读提示。
    if sys.version_info < (3, 11):  # noqa: UP036
        print("需要 Python 3.11+，请使用 E:/project/bot/.conda 中的 Python。", file=sys.stderr)
        return 1
    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "src"
    if not (source / "wechat_bot" / "console.py").is_file():
        print("缺少 src/wechat_bot/console.py，请使用完整的项目目录。", file=sys.stderr)
        return 1
    # 优先使用当前项目的代码，避免误导入其他环境中同名或旧版本的包。
    sys.path.insert(0, str(source))
    from wechat_bot.console import main

    # 默认配置绑定脚本所在项目，而不是调用者的当前目录；显式 --config 仍按用户传值。
    return main(default_config=project_root / "config" / "deepseek.toml")


if __name__ == "__main__":
    raise SystemExit(run())
