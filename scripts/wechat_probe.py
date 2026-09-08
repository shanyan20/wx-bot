"""Read-only profile preflight. No typing, clicking, polling loop or model requests."""

import argparse
import asyncio
import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_bot.adapters.windows_uia import WindowsUIAAdapter  # noqa: E402


async def probe(path):
    adapter = None
    try:
        profile = tomllib.loads(path.read_text(encoding="utf-8"))["windows"]
        adapter = WindowsUIAAdapter(profile)
        counts = []
        for chat in adapter.chats.values():
            messages = await adapter._call(adapter._snapshot, chat)
            counts.append(len(messages))
        return {"status": "snapshot_readable", "message_counts": counts,
                "read_only": True, "live_send_verified": False,
                "note": "Single snapshot cannot prove stable IDs or delivery."}
    except Exception as exc:
        # Never include exception text: third-party UI errors may contain chat text.
        return {"status": "blocked", "error_type": type(exc).__name__,
                "read_only": True, "live_send_verified": False}
    finally:
        if adapter is not None:
            await adapter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.config))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "snapshot_readable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
