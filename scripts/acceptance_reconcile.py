"""Read-only WeChat check for an interrupted send; never resends or approves C."""

import argparse
import time
from pathlib import Path

from wechat_bot.acceptance import ReviewStore
from wechat_bot.adapters.native_review import NativeReview, text_content
from wechat_bot.locking import InstanceLock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    with InstanceLock(project / "data/control/session.lock"):
        store = ReviewStore(project / "data/acceptance/reviews.sqlite3")
        backend = NativeReview(project)
        try:
            recovery = store.recover()
            chats = [c for c in backend.candidates() if c["window_title"] == args.title]
            if len(chats) != 1:
                raise ValueError("Exact independent window missing or ambiguous")
            backend.prepare_connect(chats)
            backend.connect()
            chat = chats[0]
            rows = backend.read_rows(chat)
            resolved = 0
            for case in store.pending_reviews(backend.root.parent.name, chat["id"]):
                if case.phase != "uncertain":
                    continue
                sent = store.conn.execute(
                    "SELECT max(recorded_at) FROM review_events "
                    "WHERE message_key=? AND phase='sending'",
                    (case.packet.key,)).fetchone()[0]
                if sent is None or sent > time.time():
                    continue
                matching = [r for r in rows if r["sender"] == backend.self_id and r["kind"] == 1
                            and int(sent) - 1 <= r["timestamp"] <= sent + 120
                            and text_content(r["content"] or r["compressed"]) == case.reply]
                if len(matching) == 1:
                    case.phase = "c_review"
                    case.evidence = "恢复检查发现匹配出站记录，仍待人工确认：" + matching[0]["key"]
                    store.save(case)
                    resolved += 1
            print({"recovery": recovery, "matching_outgoing": resolved,
                   "human_c_pass": False, "sent_by_this_script": False})
        finally:
            backend.close()
            store.close()


if __name__ == "__main__":
    main()
