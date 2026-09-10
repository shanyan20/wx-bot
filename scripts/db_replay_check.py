"""Replay two local metadata samples into an isolated Store; no model or send adapter."""

import json
import tempfile
from pathlib import Path

from wechat_bot.domain import Message
from wechat_bot.policy import Decision
from wechat_bot.storage import Store


def main():
    root = Path("data/db-probe")
    samples = [json.loads((root / name).read_text(encoding="utf-8"))
               for name in ("metadata-first.json", "metadata.json")]
    batches = [[row for shard in sample for row in shard.get("metadata", [])]
               for sample in samples]
    keys = [{row["message_key"] for row in batch} for batch in batches]
    if any(len(keyset) != len(batch) for keyset, batch in zip(keys, batches, strict=True)):
        raise ValueError("Duplicate native key within sample")
    shared = keys[0] & keys[1]
    before = {r["message_key"]: r for r in batches[0]}
    after = {r["message_key"]: r for r in batches[1]}
    if any(before[key] != after[key] for key in shared):
        raise ValueError("Metadata changed for the same key; investigate before enabling")
    inserted = []
    with tempfile.TemporaryDirectory(prefix="replay-", dir=root) as folder:
        # Reopen the database between batches to exercise persistent SQL deduplication.
        for batch in batches:
            store = Store(Path(folder) / "metadata-test.sqlite3")
            try:
                count = 0
                for row in batch:
                    identity = json.loads(row["message_key"])
                    message = Message(
                        "wechat_db_metadata_test", row["message_key"], identity[4],
                        row["sender_id"], "", row["created_at"], kind="metadata_only")
                    count += store.ingest(message, Decision(False, reason="metadata_only"),
                                          row["created_at"])
                inserted.append(count)
                assert not store.conn.execute(
                    "SELECT 1 FROM jobs WHERE state != 'ignored' LIMIT 1").fetchone()
            finally:
                store.close()
    expected = [len(keys[0]), len(keys[1] - keys[0])]
    if inserted != expected:
        raise ValueError("Persistent deduplication failed")
    result = {"sample_counts": [len(b) for b in batches], "shared_keys": len(shared),
              "same_key_set": keys[0] == keys[1], "same_metadata_for_shared": True,
              "inserted_per_pass": inserted, "outbound_jobs": 0,
              "contains_message_bodies": False}
    (root / "replay-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
