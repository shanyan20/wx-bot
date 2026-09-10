"""Query ONLY the pinned independent chat's native metadata, from an in-memory snapshot."""

import hashlib
import json
import sqlite3
from pathlib import Path

from native_probe import one_window

from wechat_bot.adapters.native_identity import native_message_key
from wechat_bot.adapters.sqlcipher_snapshot import decode_snapshot


def check_binding(binding):
    import psutil
    from pywinauto import Desktop

    if (psutil.Process(binding["pid"]).create_time() != binding["started"]
            or one_window(binding["title"], binding["pid"]) != binding["hwnd"]):
        raise ValueError("Pinned process/window no longer matches")
    window = Desktop(backend="uia").window(handle=binding["hwnd"]).wrapper_object()
    if (window.class_name() != "mmui::ChatSingleWindow"
            or window.element_info.automation_id != "ChatSingleWindow" + binding["conversation"]):
        raise ValueError("Native conversation identity changed")


def main():
    import win32crypt

    _, payload = win32crypt.CryptUnprotectData(Path("data/db-probe/keys.dpapi").read_bytes(),
                                              None, None, None, 1)
    binding = json.loads(payload)
    check_binding(binding)
    root = Path(binding["root"]).resolve()
    conversation = binding["conversation"]
    table = "Msg_" + hashlib.md5(conversation.encode()).hexdigest()
    results = []
    for source, secret in binding["keys"].items():
        path = Path(source).resolve()
        if path.parent != root / "message":
            raise ValueError("Database outside bound message directory")
        wal_path = Path(str(path) + "-wal")
        database = path.read_bytes()
        wal = wal_path.read_bytes() if wal_path.exists() else b""
        # Refuse a changing copy; no source SQLite connections, locks or checkpoint calls.
        if (hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(database).digest()
                or (wal_path.read_bytes() if wal_path.exists() else b"") != wal):
            raise ValueError("Database/WAL changed during snapshot capture; retry read-only")
        decoded, stats = decode_snapshot(database, wal, bytes.fromhex(secret["key"]),
                                         bytes.fromhex(secret["salt"]))
        conn = sqlite3.connect(":memory:")
        try:
            conn.deserialize(decoded)
            conn.execute("PRAGMA trusted_schema=OFF")
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA temp_store=MEMORY")
            if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Decoded SQLite integrity check failed")
            found = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                                 (table,)).fetchall()
            if not found:
                results.append({"shard": path.name, "target_found": False, **stats})
                continue
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            required = {"local_id", "server_id", "real_sender_id", "create_time", "local_type"}
            if not required.issubset(columns):
                raise ValueError("Native metadata schema differs from reviewed schema")
            rows = conn.execute(f'SELECT local_id,server_id,real_sender_id,create_time,local_type '
                                f'FROM "{table}" ORDER BY local_id DESC LIMIT 20').fetchall()
            # Database salt is a probe lineage fingerprint, NOT a proven restore detector.
            generation = hashlib.sha256(bytes.fromhex(secret["salt"])).hexdigest()
            observations = [
                {"local_id": row[0], "server_id": row[1], "sender_index": row[2],
                 "created_at": row[3], "kind": row[4],
                 "message_key": native_message_key(
                     account=root.parent.name, generation=generation, shard=path.name,
                     conversation=conversation, table=table, local_id=row[0])}
                for row in rows]
            name_table = conn.execute("SELECT name FROM sqlite_master WHERE name='Name2Id' "
                                      "AND type='table'").fetchone()
            sender_schema = ([row[1] for row in conn.execute('PRAGMA table_info("Name2Id")')]
                             if name_table else [])
            if "user_name" not in sender_schema:
                raise ValueError("Native sender mapping is unavailable")
            for observation in observations:
                mapping = conn.execute('SELECT user_name FROM "Name2Id" WHERE rowid=?',
                                       (observation["sender_index"],)).fetchall()
                if len(mapping) != 1 or not mapping[0][0]:
                    raise ValueError("Native sender mapping is missing or ambiguous")
                observation["sender_id"] = mapping[0][0]
            results.append({"shard": path.name, "target_found": True, **stats,
                            "columns": columns, "sender_map_columns": sender_schema,
                            "metadata": observations})
        finally:
            conn.close()
    check_binding(binding)
    output = Path("data/db-probe/metadata.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{"target_found": r["target_found"], "pages": r["pages"],
                       "committed_wal_frames": r["committed_wal_frames"],
                       "metadata_rows": len(r.get("metadata", [])),
                       "sender_map_columns": r.get("sender_map_columns", [])}
                      for r in results]))


if __name__ == "__main__":
    main()
