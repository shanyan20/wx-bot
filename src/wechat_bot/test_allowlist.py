"""Exact local native identity pins for the two authorized private test contacts."""

import json

TEST_CONTACTS = ("憨憨的小憨憨", "shanyan")


def allowed_candidates(project, candidates):
    path = project / "data/acceptance/test_contacts.json"
    pins = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(pins, dict) or set(pins) != set(TEST_CONTACTS)
            or any(not isinstance(v, str) or not v for v in pins.values())
            or len(set(pins.values())) != 2):
        raise ValueError("测试白名单必须绑定两位指定联系人的不同原生会话 ID")
    result = []
    for name in TEST_CONTACTS:
        matches = [c for c in candidates if c["window_title"] == name]
        if len(matches) > 1:
            raise ValueError(f"测试联系人存在同名窗口：{name}")
        if matches:
            chat = matches[0]
            if chat["chat_type"] != "private" or chat["id"] != pins[name]:
                raise ValueError(f"测试联系人原生身份不匹配：{name}")
            result.append(chat)
    return result
