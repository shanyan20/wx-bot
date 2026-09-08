"""SQLite 同时充当消息收件箱、持久化工作队列和发送箱。

事务很短，不跨 await；单个运行实例由文件锁保证，管理 CLI 可独立连接。
发送前先提交 sending，崩溃恢复为 uncertain，从而避免无法确认的自动重发。
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from wechat_bot.domain import Job, Message
from wechat_bot.policy import Decision

ACTIVE = "'pending','processing','ready','sending','uncertain'"


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.conn.close()
            raise RuntimeError(f"不支持的数据库版本 {version}，请使用匹配版本程序")
        # TODO(T08/T09)：正文保留/去重墓碑与后续 schema 迁移尚未实现。
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                message_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                prompt TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'pending','processing','ready','sending','sent',
                    'uncertain','failed','ignored','expired','canceled')),
                reason TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                send_attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                reply TEXT,
                receipt TEXT,
                UNIQUE(source, message_id)
            );
            CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, next_at, id);
            CREATE INDEX IF NOT EXISTS jobs_conversation ON jobs(source, conversation_id, id);
            CREATE INDEX IF NOT EXISTS jobs_history ON jobs(session_id, state, id);
            CREATE TABLE IF NOT EXISTS pauses (
                conversation_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=1;
        """)

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def ingest(self, message: Message, decision: Decision, now: float) -> bool:
        message.validate()
        with self.transaction():
            paused = self.is_paused(message.conversation_id)
            state = "pending" if decision.accept and not paused else "ignored"
            cursor = self.conn.execute(
                """INSERT OR IGNORE INTO jobs
                (source,message_id,conversation_id,session_id,payload,prompt,state,reason,
                 created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (message.source, message.message_id, message.conversation_id, message.session_id,
                 json.dumps(asdict(message), ensure_ascii=False), decision.prompt, state,
                 "paused" if paused else decision.reason, message.created_at, now),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(row["id"], Message(**json.loads(row["payload"])), row["prompt"],
                   row["attempts"], row["reply"])

    def recover(self, now: float) -> None:
        """仅在持有实例锁之后调用，不能把其他活跃实例的工作误判为崩溃。"""
        with self.transaction():
            self.conn.execute(
                "UPDATE jobs SET state='pending',updated_at=? WHERE state='processing'", (now,)
            )
            self.conn.execute(
                """UPDATE jobs SET state='uncertain',reason='restart_during_send',updated_at=?
                   WHERE state='sending'""", (now,)
            )

    def is_paused(self, conversation_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM pauses WHERE conversation_id IN ('*',?)", (conversation_id,)
        ).fetchone() is not None

    def pause(self, conversation_id: str, now: float) -> None:
        # TODO(T05)：目前由 CLI 主动接管；尚未从微信事件自动判断人工接管。
        with self.transaction():
            self.conn.execute("INSERT OR IGNORE INTO pauses VALUES (?)", (conversation_id,))
            self.conn.execute(
                """UPDATE jobs SET state='canceled',reason='manual_pause',updated_at=?
                WHERE (?='*' OR conversation_id=?) AND state IN ('pending','processing','ready')""",
                (now, conversation_id, conversation_id),
            )

    def resume(self, conversation_id: str) -> None:
        self.conn.execute("DELETE FROM pauses WHERE conversation_id=?", (conversation_id,))

    def cancel_queued(self, now: float) -> None:
        self.conn.execute(
            """UPDATE jobs SET state='canceled',reason='pause_file',updated_at=?
               WHERE state IN ('pending','processing','ready')""", (now,)
        )

    def expire(self, now: float, ttl: float) -> None:
        self.conn.execute(
            """UPDATE jobs SET state='expired',reason='ttl',updated_at=?
               WHERE state IN ('pending','processing','ready') AND created_at < ?""",
            (now, now - ttl),
        )

    def claim(self, now: float, max_attempts: int) -> Job | None:
        with self.transaction():
            # 崩溃也消耗一次尝试，避免每次启动都无限重新请求模型。
            self.conn.execute(
                """UPDATE jobs SET state='failed',reason='attempts_exhausted',updated_at=?
                   WHERE state='pending' AND attempts >= ?""", (now, max_attempts)
            )
            row = self.conn.execute(f"""
                SELECT j.* FROM jobs j WHERE j.state='pending' AND j.next_at <= ?
                AND NOT EXISTS (SELECT 1 FROM pauses p
                    WHERE p.conversation_id IN ('*', j.conversation_id))
                AND NOT EXISTS (SELECT 1 FROM jobs previous
                    WHERE previous.source=j.source AND previous.conversation_id=j.conversation_id
                    AND previous.id < j.id AND previous.state IN ({ACTIVE}))
                ORDER BY j.id LIMIT 1
            """, (now,)).fetchone()
            if row is None:
                return None
            self.conn.execute(
                "UPDATE jobs SET state='processing',attempts=attempts+1,updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            return self._job(self.conn.execute(
                "SELECT * FROM jobs WHERE id=?", (row["id"],)
            ).fetchone())

    def history(self, session_id: str, turns: int) -> list[dict[str, str]]:
        rows = self.conn.execute(
            """SELECT prompt,reply FROM jobs WHERE session_id=? AND state='sent'
               ORDER BY id DESC LIMIT ?""", (session_id, turns)
        ).fetchall()
        history = []
        for row in reversed(rows):
            history.extend([
                {"role": "user", "content": row["prompt"]},
                {"role": "assistant", "content": row["reply"]},
            ])
        return history

    def ready(self, job_id: int, reply: str, now: float, reason: str = "") -> None:
        # 人工暂停/过期可能已取消 processing，因此一定要带旧状态条件。
        self.conn.execute(
            """UPDATE jobs SET state='ready',reply=?,reason=?,next_at=0,updated_at=?
               WHERE id=? AND state='processing'""", (reply, reason, now, job_id)
        )

    def retry_model(self, job: Job, now: float, delay: float, reason: str) -> None:
        self.conn.execute(
            """UPDATE jobs SET state='pending',reason=?,next_at=?,updated_at=?
               WHERE id=? AND state='processing'""", (reason, now + delay, now, job.id)
        )

    def claim_send(self, now: float, interval: float) -> Job | None:
        with self.transaction():
            previous = self.conn.execute(
                "SELECT value FROM meta WHERE key='last_send_at'"
            ).fetchone()
            if previous and now - float(previous[0]) < interval:
                return None
            row = self.conn.execute(
                """SELECT * FROM jobs j WHERE state='ready' AND next_at <= ?
                   AND NOT EXISTS (SELECT 1 FROM pauses p
                       WHERE p.conversation_id IN ('*',j.conversation_id))
                   ORDER BY id LIMIT 1""", (now,)
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                """UPDATE jobs SET state='sending',send_attempts=send_attempts+1,updated_at=?
                   WHERE id=?""", (now, row["id"])
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('last_send_at',?)", (str(now),)
            )
            return self._job(row)

    def sent(self, job_id: int, receipt: str, now: float) -> None:
        self.conn.execute(
            """UPDATE jobs SET state='sent',receipt=?,updated_at=?
               WHERE id=? AND state='sending'""", (receipt, now, job_id)
        )

    def uncertain(self, job_id: int, now: float, reason: str) -> None:
        self.conn.execute(
            """UPDATE jobs SET state='uncertain',reason=?,updated_at=?
               WHERE id=? AND state='sending'""", (reason, now, job_id)
        )

    def cancel_before_send(self, job_id: int, now: float, reason: str) -> None:
        """只供确认尚未调用外部发送的执行路径使用。"""
        self.conn.execute(
            """UPDATE jobs SET state='canceled',reason=?,updated_at=?
               WHERE id=? AND state='sending'""", (reason, now, job_id)
        )

    def not_sent(self, job_id: int, now: float, maximum: int, delay: float) -> None:
        self.conn.execute(
            """UPDATE jobs SET state=CASE WHEN send_attempts >= ? THEN 'failed' ELSE 'ready' END,
               reason='not_sent',next_at=?,updated_at=? WHERE id=? AND state='sending'""",
            (maximum, now + delay, now, job_id),
        )

    def resolve(self, job_id: int, state: str, now: float) -> None:
        if state not in ("sent", "canceled"):
            raise ValueError("不确定任务只能人工标记 sent 或 canceled")
        changed = self.conn.execute(
            """UPDATE jobs SET state=?,reason='manually_resolved',updated_at=?
               WHERE id=? AND state='uncertain'""", (state, now, job_id)
        ).rowcount
        if not changed:
            raise ValueError("任务不存在或并非 uncertain 状态")

    def status(self, now: float) -> dict:
        counts = {r[0]: r[1] for r in self.conn.execute(
            "SELECT state,COUNT(*) FROM jobs GROUP BY state"
        )}
        oldest = self.conn.execute(
            f"SELECT MIN(created_at) FROM jobs WHERE state IN ({ACTIVE})"
        ).fetchone()[0]
        return {"counts": counts, "oldest_active_age_seconds":
                max(0.0, now - oldest) if oldest is not None else 0.0,
                "paused": [r[0] for r in self.conn.execute("SELECT * FROM pauses")]}

    def recent(self, limit: int = 20) -> list[dict]:
        # 管理命令默认不显示聊天正文，降低终端/日志意外泄漏。
        return [dict(r) for r in self.conn.execute(
            """SELECT id,conversation_id,state,reason,attempts,send_attempts,updated_at
               FROM jobs ORDER BY id DESC LIMIT ?""", (limit,)
        )]

    def message_result(self, source: str, message_id: str) -> dict | None:
        """按入站身份读取任务结果，供交互客户端等待当前消息，不暴露数据库连接。"""
        row = self.conn.execute(
            "SELECT id,state,reason,reply FROM jobs WHERE source=? AND message_id=?",
            (source, message_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def backup(self, target: Path) -> None:
        if target.resolve() == Path(self.conn.execute("PRAGMA database_list").fetchone()[2]):
            raise ValueError("备份目标不能是当前数据库")
        target.parent.mkdir(parents=True, exist_ok=True)
        # 不直接复制活跃的 sqlite 文件；backup API 会包含 WAL 中已经提交的数据。
        with sqlite3.connect(target) as destination:
            self.conn.backup(destination)
