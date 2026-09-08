"""结构化事件日志和心跳。默认不记录消息正文、提示词、令牌及异常原始描述。"""

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    logger = logging.getLogger("wechat_bot")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False


def event(name: str, **fields) -> None:
    logging.getLogger("wechat_bot").info(json.dumps({"event": name, **fields}, ensure_ascii=False))


def write_heartbeat(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)

