import json
from dataclasses import replace

import pytest

from wechat_bot.acceptance import Packet, ReviewCase, ReviewStore


def packet(key="one", conversation="alice", **changes):
    return replace(Packet(key, "account", conversation, "private", conversation,
                          100, "text", "hello"), **changes)


def complete(case):
    case.approve_a()
    case.begin_model()
    case.model_result("reply")
    case.approve_b()
    case.begin_draft()
    case.drafted()
    case.begin_send()
    case.sent("native_db_outgoing:test")
    case.approve_c()


def test_each_human_verdict_is_independent_and_required():
    case = ReviewCase(packet())
    for action in (case.begin_model, case.approve_b, case.begin_draft,
                   case.begin_send, case.approve_c):
        with pytest.raises(ValueError):
            action()
    case.approve_a()
    case.begin_model()
    case.model_result("reply")
    assert case.a_pass and not case.b_pass and not case.c_pass
    with pytest.raises(ValueError):
        case.begin_draft()
    case.approve_b()
    case.begin_draft()
    case.drafted()
    assert case.phase == "draft_review" and not case.c_pass
    case.begin_send()
    case.sent("native_db_outgoing:verified")
    assert not case.c_pass
    case.approve_c()
    assert case.phase == "complete" and case.c_pass


@pytest.mark.parametrize("changes", [dict(kind="image"), dict(kind="unsupported"), dict(text="")])
def test_unreadable_input_never_passes_a(changes):
    with pytest.raises(ValueError):
        ReviewCase(packet(**changes)).approve_a()


def test_image_cannot_change_between_review_and_model(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"test fixture before")
    case = ReviewCase(packet(kind="image", text="", image=str(image)))
    case.approve_a()
    image.write_bytes(b"test fixture after")
    with pytest.raises(ValueError, match="改变"):
        case.begin_model()


def test_histories_are_separate_and_only_human_completed_cases_enter_history(tmp_path):
    store = ReviewStore(tmp_path / "review.sqlite3")
    try:
        alice, bob = ReviewCase(packet()), ReviewCase(packet("two", "bob"))
        complete(alice)
        bob.approve_a()
        bob.begin_model()
        bob.model_result("unapproved")
        store.save(alice)
        store.save(bob)
        assert store.history(alice.packet.session, 3)[-1]["content"] == "reply"
        assert store.history(bob.packet.session, 3) == []
        assert store.history(alice.packet.session, 0) == []
        other_account = replace(alice.packet, account="other")
        assert store.history(other_account.session, 3) == []
        group = replace(alice.packet, chat_type="group")
        assert group.session != replace(group, sender="bob").session
        output = tmp_path / "report.json"
        store.export(output)
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report[0]["c_pass"] and not report[1]["b_pass"]
        assert store.conn.execute("SELECT count(*) FROM review_events").fetchone()[0] == 2
    finally:
        store.close()
    reopened = ReviewStore(tmp_path / "review.sqlite3")
    try:
        assert reopened.handled("one") and reopened.handled("two")
    finally:
        reopened.close()


@pytest.mark.parametrize("phase", ["canceled", "rejected", "uncertain", "failed", "complete"])
def test_terminal_case_cannot_send_again(phase):
    case = ReviewCase(packet(), phase=phase)
    with pytest.raises(ValueError):
        case.begin_send()


def test_restart_marks_inflight_send_uncertain_without_claiming_human_success(tmp_path):
    path = tmp_path / "reviews.sqlite3"
    store = ReviewStore(path)
    store.save(ReviewCase(packet(), phase="sending", reply="answer", a_pass=True, b_pass=True))
    store.save(ReviewCase(packet("draft"), phase="draft_running"))
    store.close()
    store = ReviewStore(path)
    try:
        result = store.recover()
        assert result == {"interrupted": 2, "uncertain": 1}
        rows = store.conn.execute("SELECT phase,c_pass FROM reviews ORDER BY rowid").fetchall()
        assert rows == [("uncertain", 0), ("canceled", 0)]
        assert store.recover() == {"interrupted": 0, "uncertain": 1}
    finally:
        store.close()
