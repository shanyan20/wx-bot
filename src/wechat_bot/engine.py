"""应用编排：跨会话并发生成，同一会话有序；所有发送只有一个消费者。"""

import asyncio
import random
import time

from wechat_bot.adapters.base import ChatAdapter
from wechat_bot.config import Settings
from wechat_bot.domain import Job, ModelError, NotSentError
from wechat_bot.observability import event, write_heartbeat
from wechat_bot.policy import Decision, decide
from wechat_bot.services.model import ReplyModel
from wechat_bot.storage import Store


class Engine:
    def __init__(self, settings: Settings, store: Store, adapter: ChatAdapter, model: ReplyModel):
        self.settings, self.store, self.adapter, self.model = settings, store, adapter, model
        self.generating: set[asyncio.Task] = set()
        self.sending: asyncio.Task | None = None
        self.last_poll_ok: float | None = None
        self.poll_failures = 0
        self.next_poll_at = 0.0

    def delay(self, attempt: int) -> float:
        return min(60.0, self.settings.retry_base_seconds * 2 ** min(attempt - 1, 6)) \
            + random.uniform(0, 0.5)

    async def generate(self, job: Job) -> None:
        history = self.store.history(job.message.session_id, self.settings.context_turns)
        try:
            reply = await asyncio.wait_for(
                self.model.reply(job.prompt, history), timeout=self.settings.model_timeout_seconds
            )
            if not isinstance(reply, str) or not reply.strip():
                raise ModelError("empty_reply")
        except (ModelError, TimeoutError) as exc:
            now = time.time()
            event("model_failed", job_id=job.id, error_type=type(exc).__name__)
            if job.attempts >= self.settings.max_attempts:
                self.store.ready(job.id, self.settings.fallback[:self.settings.max_reply_chars],
                                 now, "model_fallback")
            else:
                self.store.retry_model(job, now, self.delay(job.attempts), "model_error")
        else:
            self.store.ready(job.id, reply.strip()[:self.settings.max_reply_chars], time.time())

    async def send(self, job: Job) -> None:
        if self.settings.pause_file.exists() or self.store.is_paused(job.message.conversation_id):
            # claim_send 后、真正调用适配器前再检查暂停；尚未发送，可以安全取消。
            self.store.cancel_before_send(job.id, time.time(), "paused_before_send")
            return
        # 重启时可能换了白名单/TTL：不能仅依据旧配置下已经生成的 ready 状态发送。
        decision = decide(job.message, self.settings, time.time())
        if not decision.accept:
            self.store.cancel_before_send(job.id, time.time(), decision.reason)
            return
        try:
            receipt = await self.adapter.send(job)
        except NotSentError:
            self.store.not_sent(job.id, time.time(), self.settings.max_attempts,
                                self.settings.retry_base_seconds)
            event("send_not_sent", job_id=job.id)
        except Exception as exc:
            # 包括未知异常：宁可交给人工核对，也不冒险重发。
            self.store.uncertain(job.id, time.time(), type(exc).__name__)
            event("send_uncertain", job_id=job.id, error_type=type(exc).__name__)
        else:
            self.store.sent(job.id, receipt.evidence, time.time())
            event("send_confirmed", job_id=job.id)

    async def tick(self) -> None:
        """单次调度，测试可直接调用；完成任务的异常必须被观察，不能悄悄丢失。"""
        for task in list(self.generating):
            if task.done():
                task.result()
                self.generating.remove(task)
        if self.sending and self.sending.done():
            self.sending.result()
            self.sending = None
        now = time.time()
        paused = self.settings.pause_file.exists()
        if paused:
            self.store.cancel_queued(now)
        self.store.expire(now, self.settings.message_ttl_seconds)
        if now >= self.next_poll_at:
            try:
                batch = await self.adapter.poll()
            except Exception as exc:
                self.poll_failures += 1
                self.next_poll_at = now + self.delay(self.poll_failures)
                event("poll_failed", error_type=type(exc).__name__, count=self.poll_failures)
            else:
                # DB 写入异常向上传播并终止进程，不 ack，不假装消息已经可靠接收。
                for message in batch:
                    decision = Decision(False, reason="pause_file") if paused else decide(
                        message, self.settings, now
                    )
                    if self.store.ingest(message, decision, now):
                        event("message_ingested", accepted=decision.accept)
                await self.adapter.ack(batch)
                self.last_poll_ok, self.poll_failures = time.time(), 0
                self.next_poll_at = now + self.settings.poll_seconds
        # 接入层异常时停止新生成/新发送，已有模型请求可完成落库。
        if not paused and self.poll_failures == 0:
            while len(self.generating) < self.settings.workers:
                job = self.store.claim(now, self.settings.max_attempts)
                if job is None:
                    break
                self.generating.add(asyncio.create_task(self.generate(job)))
            if self.sending is None:
                job = self.store.claim_send(now, self.settings.send_interval_seconds)
                if job:
                    self.sending = asyncio.create_task(self.send(job))
        write_heartbeat(self.settings.heartbeat, {
            "timestamp": time.time(), "last_poll_ok": self.last_poll_ok,
            "poll_failures": self.poll_failures, "pause_file": paused,
            **self.store.status(time.time()),
        })

    async def run(self, stop: asyncio.Event) -> None:
        event("started", adapter=self.settings.adapter, model=self.settings.model)
        try:
            while not stop.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=min(self.settings.poll_seconds, 0.5)
                    )
                except TimeoutError:
                    pass
        finally:
            await self.close()

    async def close(self) -> None:
        for task in self.generating:
            task.cancel()
        await asyncio.gather(*self.generating, return_exceptions=True)
        # 不强制取消正在进行的 UI 发送：线程取消不能撤回已发生的界面操作。
        # TODO(T06): OS UIA 调用可能阻塞，生产版应采用可监督的独立进程。
        try:
            if self.sending:
                await self.sending
        finally:
            try:
                await self.adapter.close()
            finally:
                await self.model.close()
        event("stopped")
