import json

import pytest

from wechat_bot.acceptance import Packet, ReviewCase, ReviewStore
from wechat_bot.test_allowlist import TEST_CONTACTS, allowed_candidates


def test_native_pins_exclude_outsiders_and_reject_impersonation(tmp_path):
    folder = tmp_path / "data/acceptance"
    folder.mkdir(parents=True)
    (folder / "test_contacts.json").write_text(json.dumps(dict(zip(
        TEST_CONTACTS, ("friend1", "friend2"), strict=True))), encoding="utf-8")
    rows = [dict(window_title=n, id=f"friend{i+1}", chat_type="private")
            for i, n in enumerate(TEST_CONTACTS)]
    outsider = dict(window_title="someone else", id="outside", chat_type="private")
    assert allowed_candidates(tmp_path, rows + [outsider]) == rows
    with pytest.raises(ValueError, match="同名"):
        allowed_candidates(tmp_path, rows + [rows[0]])
    for change in ({"id": "impostor"}, {"chat_type": "group"}):
        with pytest.raises(ValueError, match="身份"):
            allowed_candidates(tmp_path, [{**rows[0], **change}, rows[1]])


def test_auto_delivery_history_isolated_and_survives_restart(tmp_path):
    store = ReviewStore(tmp_path / "reviews.sqlite3")
    for friend in ("friend1", "friend2"):
        packet = Packet(friend, "account", friend, "private", friend, 1, "text", friend)
        case = ReviewCase(packet, review_policy="automatic")
        case.automatic_input()
        case.begin_model()
        case.model_result(f"reply to {friend}")
        case.automatic_reply()
        case.begin_draft()
        case.drafted()
        case.begin_send()
        with pytest.raises(ValueError):
            case.automatic_sent("clicked only")
        case.automatic_sent(f"native_db_outgoing:{friend}")
        assert not any((case.a_pass, case.b_pass, case.c_pass))
        store.save(case)
        assert store.history(packet.session, 6) == [
            {"role": "user", "content": friend},
            {"role": "assistant", "content": f"reply to {friend}"}]
    assert store.recover()["interrupted"] == 0
    assert store.handled("friend1") and store.handled("friend2")
    store.close()
