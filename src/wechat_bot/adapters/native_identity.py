"""Identity contract for a future authorized database reader, not a UIA fallback.

All fields must come from a verified native reader. Generation identifies an unchanged
database lineage and must be rotated on restore/rebuild. No text/runtime-ID inference.
Server IDs can appear after sending, so they must not replace an existing local key.
"""

import json


def native_message_key(*, account: str, generation: str, shard: str,
                       conversation: str, table: str, local_id: int) -> str:
    fields = (account, generation, shard, conversation, table)
    if any(not isinstance(value, str) or not value.strip() for value in fields):
        raise ValueError("Native account/database/conversation identity is required")
    if type(local_id) is not int or local_id <= 0:
        raise ValueError("Native local_id must be a positive integer")
    return json.dumps(["wechat-db-local-v1", *fields, local_id],
                      ensure_ascii=False, separators=(",", ":"))
