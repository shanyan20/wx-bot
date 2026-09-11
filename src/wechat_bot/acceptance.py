"""Human-reviewed A/B/C cases. No network or desktop side effects in this module."""

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Packet:
    key: str
    account: str
    conversation: str
    chat_type: str
    sender: str
    timestamp: int
    kind: str
    text: str
    image: str = ""
    note: str = ""

    @property
    def session(self):
        return json.dumps([self.account, self.chat_type, self.conversation,
                           self.sender if self.chat_type == "group" else ""], ensure_ascii=False)

    def fingerprint(self):
        data = json.dumps(asdict(self), sort_keys=True).encode()
        if self.image:
            data += Path(self.image).read_bytes()
        return hashlib.sha256(data).hexdigest()


@dataclass
class ReviewCase:
    packet: Packet
    phase: str = "a_review"
    reply: str = ""
    digest: str = ""
    evidence: str = ""
    a_pass: bool = False
    b_pass: bool = False
    c_pass: bool = False

    def require(self, *phases):
        if self.phase not in phases:
            raise ValueError("当前阶段不允许该操作")

    def approve_a(self):
        self.require("a_review")
        if self.packet.kind not in ("text", "image"):
            raise ValueError("消息类型未实现，不能判为读取通过")
        if self.packet.kind == "image" and not self.packet.image:
            raise ValueError("未取得真实图片，不能判为读取通过")
        if self.packet.kind == "text" and not self.packet.text:
            raise ValueError("文字解析为空")
        self.digest = self.packet.fingerprint()
        self.a_pass, self.phase = True, "a_pass"

    def begin_model(self):
        self.require("a_pass")
        if self.packet.fingerprint() != self.digest:
            raise ValueError("已审阅消息或图片发生改变，请重新读取")
        self.phase = "model_running"

    def model_result(self, reply):
        self.require("model_running")
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("模型回复为空")
        self.reply, self.phase = reply, "b_review"

    def approve_b(self):
        self.require("b_review")
        self.b_pass, self.phase = True, "b_pass"

    def begin_draft(self):
        self.require("b_pass")
        self.phase = "draft_running"

    def drafted(self):
        self.require("draft_running")
        self.phase = "draft_review"

    def begin_send(self):
        self.require("draft_review")
        self.phase = "sending"

    def sent(self, evidence):
        self.require("sending")
        self.evidence, self.phase = evidence, "c_review"

    def approve_c(self):
        self.require("c_review")
        self.c_pass, self.phase = True, "complete"


class ReviewStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS reviews (
            message_key TEXT PRIMARY KEY, session TEXT NOT NULL, body TEXT NOT NULL,
            reply TEXT NOT NULL, phase TEXT NOT NULL, evidence TEXT NOT NULL,
            a_pass INTEGER NOT NULL, b_pass INTEGER NOT NULL, c_pass INTEGER NOT NULL)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS review_events (
            id INTEGER PRIMARY KEY, message_key TEXT NOT NULL, phase TEXT NOT NULL,
            recorded_at REAL NOT NULL, a_pass INTEGER, b_pass INTEGER, c_pass INTEGER)""")
        self.conn.commit()

    def recover(self):
        """Call only while holding the acceptance session lock; never infer a human verdict."""
        interrupted = self.conn.execute(
            "SELECT message_key,phase FROM reviews WHERE phase NOT IN "
            "('complete','rejected','failed','canceled','uncertain','c_review')").fetchall()
        for key, phase in interrupted:
            status = "uncertain" if phase == "sending" else "canceled"
            self.conn.execute("UPDATE reviews SET phase=?,evidence=evidence || ? "
                              "WHERE message_key=?",
                              (status, "\n程序中断；未自动重放。请核对微信与验收记录。", key))
            self.conn.execute("INSERT INTO review_events VALUES(NULL,?,?,?,?,?,?)", (
                key, status, time.time(), None, None, None))
        self.conn.commit()
        return {"interrupted": len(interrupted), "uncertain": self.conn.execute(
            "SELECT count(*) FROM reviews WHERE phase='uncertain'").fetchone()[0]}

    def save(self, case):
        self.conn.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,?,?,?)", (
            case.packet.key, case.packet.session,
            json.dumps(asdict(case.packet), ensure_ascii=False),
            case.reply, case.phase, case.evidence, case.a_pass, case.b_pass, case.c_pass))
        self.conn.execute("INSERT INTO review_events VALUES(NULL,?,?,?,?,?,?)", (
            case.packet.key, case.phase, time.time(), case.a_pass, case.b_pass, case.c_pass))
        self.conn.commit()

    def handled(self, key):
        # Never silently replay a case that was drafted, sent or left uncertain after a crash.
        return self.conn.execute("SELECT 1 FROM reviews WHERE message_key=?", (key,)).fetchone()

    def pending_reviews(self, account, conversation):
        rows = self.conn.execute("SELECT body,reply,phase,evidence,a_pass,b_pass,c_pass "
                                 "FROM reviews WHERE phase IN ('c_review','uncertain')").fetchall()
        cases = []
        for row in rows:
            packet = Packet(**json.loads(row[0]))
            if packet.account == account and packet.conversation == conversation:
                cases.append(ReviewCase(packet, phase=row[2], reply=row[1], evidence=row[3],
                                        a_pass=bool(row[4]), b_pass=bool(row[5]),
                                        c_pass=bool(row[6])))
        return cases

    def history(self, session, turns):
        if type(turns) is not int or turns < 0:
            raise ValueError("历史轮数不得为负数")
        rows = self.conn.execute("SELECT body,reply FROM reviews WHERE session=? AND c_pass=1 "
                                 "ORDER BY rowid DESC LIMIT ?", (session, turns)).fetchall()
        history = []
        for raw, reply in reversed(rows):
            packet = json.loads(raw)
            text = packet["text"] or "[此前用户发送图片，以下是当时回复]"
            history.extend([{"role": "user", "content": text},
                            {"role": "assistant", "content": reply}])
        return history

    def export(self, path):
        """Local report includes actual messages; caller must keep it out of Git."""
        rows = self.conn.execute("SELECT body,reply,phase,evidence,a_pass,b_pass,c_pass "
                                 "FROM reviews ORDER BY rowid").fetchall()
        data = [{"packet": json.loads(r[0]), "reply": r[1], "phase": r[2], "evidence": r[3],
                 "a_pass": bool(r[4]), "b_pass": bool(r[5]), "c_pass": bool(r[6])} for r in rows]
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(data)

    def close(self):
        self.conn.close()
