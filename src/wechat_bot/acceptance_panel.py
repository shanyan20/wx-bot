"""Native WeChat A/B/C acceptance window. Human verdicts are never inferred."""

import argparse
import asyncio
import json
import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from wechat_bot.acceptance import ReviewCase, ReviewStore
from wechat_bot.adapters.native_review import NativeReview
from wechat_bot.config import load_settings
from wechat_bot.locking import InstanceLock
from wechat_bot.services.model import HttpModel


class AcceptancePanel:
    def __init__(self, root, project):
        self.root, self.project = root, Path(project)
        self.settings = load_settings(self.project / "config/deepseek.toml")
        self.store = ReviewStore(self.project / "data/acceptance/reviews.sqlite3")
        recovered = self.store.recover()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wechat-review")
        self.events = queue.Queue()
        self.backend = None
        self.busy, self.connected, self.closed = False, False, False
        self.epoch = 0
        self.candidates, self.tabs, self.cases = [], {}, {}
        root.title("微信 Bot · A/B/C 人工验收")
        root.geometry("1240x860")
        root.minsize(1040, 760)
        self.status = tk.StringVar(value="Bot 已关闭 · 正在检查独立窗口")
        ttk.Label(root, textvariable=self.status, font=("Microsoft YaHei UI", 13)).pack(
            anchor="w", padx=16, pady=12)
        ttk.Label(root, text=f"模型：{self.settings.model_name}　｜　每个会话独立上下文　｜　"
                  "A/B/C 均须人工判定，发送前另行确认").pack(anchor="w", padx=16)
        if recovered["uncertain"]:
            ttk.Label(root, text=f"上次有 {recovered['uncertain']} 条发送结果不确定，禁止自动重发。"
                      "请导出记录并在微信中核对。", foreground="#a02020").pack(anchor="w", padx=16)
        top = ttk.Frame(root, padding=12)
        top.pack(fill="x")
        self.windows = tk.Listbox(top, selectmode="multiple", height=4, exportselection=False)
        self.windows.pack(side="left", fill="x", expand=True)
        commands = ttk.Frame(top)
        commands.pack(side="left", padx=12)
        ttk.Button(commands, text="刷新独立窗口", command=self.refresh).pack(fill="x")
        ttk.Button(commands, text="启动 Bot / 连接所选白名单", command=self.connect).pack(fill="x")
        ttk.Button(commands, text="■ 停止 Bot", command=self.stop).pack(fill="x")
        ttk.Button(commands, text="导出本地验收报告", command=self.export).pack(fill="x")
        calibration = ttk.Frame(root, padding=(12, 0))
        calibration.pack(fill="x")
        ttk.Label(calibration, text="重新校准的独立窗口标题：").pack(side="left")
        self.calibration_title = tk.StringVar(value="shanyan")
        ttk.Entry(calibration, textvariable=self.calibration_title, width=22).pack(side="left")
        ttk.Button(calibration, text="校准当前微信（无障碍开关＋本地密钥）",
                   command=self.calibrate).pack(side="left", padx=8)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=8)
        ttk.Label(root, text="开始连接以当前消息为基线，不自动处理历史；"
                  "需要历史样本时点击对应会话的读取按钮。"
                  "停止后不再调用模型或操作微信；已执行的发送无法撤回。").pack(padx=12, pady=8)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.drain)
        root.after(2000, self.poll)
        self.submit(self.initialize, self.initialized)

    def initialize(self):
        import sys
        sys.coinit_flags = 0
        import comtypes
        comtypes.CoInitializeEx(0)
        backend = NativeReview(self.project)
        return backend, backend.candidates()

    def initialized(self, result):
        self.backend, self.candidates = result
        self.show_candidates()

    def submit(self, function, done, case=None):
        if self.busy or self.closed:
            raise ValueError("请等待当前操作完成")
        self.busy = True
        epoch = self.epoch
        def guarded():
            if epoch != self.epoch or self.closed:
                raise ValueError("操作已被停止")
            return function()
        future = self.pool.submit(guarded)
        future.add_done_callback(lambda f: self.events.put((epoch, f, done, case)))

    def drain(self):
        if self.closed:
            return
        while not self.events.empty():
            epoch, future, done, case = self.events.get()
            self.busy = False
            try:
                result = future.result()
                if epoch == self.epoch:
                    done(result)
                elif case:
                    case.phase = "uncertain" if case.phase == "sending" else "canceled"
                    self.store.save(case)
            except Exception as exc:
                if case:
                    case.phase = ("uncertain" if case.phase == "sending" else
                                  "canceled" if epoch != self.epoch else "failed")
                    case.evidence = f"{type(exc).__name__}: {exc}"
                    self.store.save(case)
                    self.display(case)
                self.stop()
                self.status.set(f"已停止：{type(exc).__name__}：{exc}")
        self.root.after(100, self.drain)

    def show_candidates(self):
        self.windows.configure(state="normal")
        self.windows.delete(0, "end")
        selection_path = self.project / "data/acceptance/allowlist.json"
        saved = json.loads(selection_path.read_text(encoding="utf-8")) \
            if selection_path.exists() else None
        for index, chat in enumerate(self.candidates):
            kind = "群聊" if chat["chat_type"] == "group" else "联系人"
            self.windows.insert("end", f"{kind} · {chat['window_title']} · {chat['id']}")
            if ((saved is None and chat["window_title"] == "shanyan")
                    or (saved is not None and [chat["chat_type"], chat["id"]] in saved)):
                self.windows.selection_set(index)
        self.status.set("Bot 已关闭 · 请选择允许读取和回复的独立窗口")

    def refresh(self):
        if self.busy or self.connected:
            return
        if self.backend is None:
            self.submit(self.initialize, self.initialized)
        else:
            self.submit(self.backend.candidates, lambda rows: (
                setattr(self, "candidates", rows), self.show_candidates()))

    def calibrate(self):
        if self.busy or self.connected:
            messagebox.showinfo("校准", "请先停止 Bot 并等待当前操作结束。")
            return
        title = self.calibration_title.get().strip()
        if not title:
            return
        self.status.set("正在校准指定独立窗口；不发送消息…")
        epoch = self.epoch

        def work():
            from wechat_bot.acceptance_setup import calibrate
            if self.backend:
                self.backend.close()
            def check():
                if epoch != self.epoch:
                    raise ValueError("校准已停止")
            calibrate(self.project, title, check)
            return self.initialize()
        self.submit(work, self.initialized)

    def connect(self):
        if self.busy or self.connected or self.backend is None:
            return
        selected = [self.candidates[i] for i in self.windows.curselection()]
        if not selected:
            messagebox.showinfo("白名单", "请至少选择一个独立聊天窗口。")
            return
        self.status.set("正在核对目标并建立新消息基线…")
        self.backend.prepare_connect(selected)
        self.submit(self.backend.connect, lambda _: self.connected_ok(selected))

    def connected_ok(self, selected):
        self.connected = True
        self.windows.configure(state="disabled")
        (self.project / "data/acceptance/allowlist.json").write_text(json.dumps(
            [[c["chat_type"], c["id"]] for c in selected]), encoding="utf-8")
        for chat in selected:
            if chat["id"] not in self.tabs:
                self.add_tab(chat)
            if hasattr(self.backend, "root"):
                for case in self.store.pending_reviews(self.backend.root.parent.name, chat["id"]):
                    if case.packet.key not in self.cases:
                        self.mount_case(case)
        self.status.set("已连接白名单 · 监听新消息 · 审阅模式（不会自动发送）")

    def add_tab(self, chat):
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=chat["window_title"])
        ttk.Label(frame, text=f"原生会话：{chat['id']}　类型：{chat['chat_type']}").pack(anchor="w")
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=8)
        for label, kind in (("A 最新消息", None), ("A 最近文字", 1), ("A 最近图片", 3)):
            ttk.Button(toolbar, text=label, command=lambda k=kind:
                       self.latest(chat["id"], k)).pack(side="left")
        picker = ttk.Combobox(toolbar, state="readonly", width=45)
        picker.pack(side="left", padx=8)
        status = tk.StringVar(value="尚无样本 · A/B/C 均未判定")
        ttk.Label(frame, textvariable=status).pack(anchor="w")
        panes = ttk.Panedwindow(frame, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=8)
        texts = []
        for title in ("A · 微信原始消息", "B · 模型回复", "C · 草稿 / 发送证据"):
            box = ttk.LabelFrame(panes, text=title, padding=6)
            text = tk.Text(box, wrap="word", width=30, height=16)
            text.pack(fill="both", expand=True)
            text.configure(state="disabled")
            texts.append(text)
            panes.add(box, weight=1)
        image_label = ttk.Label(frame)
        image_label.pack(anchor="w")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=8)
        for label, action in (("A 人工判定通过", "approve_a"), ("B 调用模型", "model"),
                              ("B 人工判定通过", "approve_b"), ("C1 填入草稿", "draft"),
                              ("C2 确认发送", "send"), ("C 人工判定通过", "approve_c")):
            ttk.Button(actions, text=label, command=lambda a=action, c=chat["id"]:
                       self.action(c, a)).pack(side="left", padx=3)
        ttk.Button(frame, text="本样本判定不通过 / 停止", command=lambda:
                   self.reject(chat["id"])).pack(anchor="w")
        ttk.Button(frame, text="A 重读当前样本（下载图片后可重试）", command=lambda:
                   self.action(chat["id"], "reload")).pack(anchor="w")
        ttk.Button(frame, text="查看本地图片", command=lambda:
                   self.view_image(chat["id"])).pack(anchor="w")
        self.tabs[chat["id"]] = dict(frame=frame, texts=texts, image=image_label,
                                     picker=picker, keys=[], status=status)
        picker.bind("<<ComboboxSelected>>", lambda _: self.select(chat["id"]))

    def active(self, conversation):
        tab = self.tabs[conversation]
        index = tab["picker"].current()
        if index < 0:
            raise ValueError("请先选择消息样本")
        return self.cases[tab["keys"][index]]

    def select(self, conversation):
        self.display(self.active(conversation))

    def view_image(self, conversation):
        import os
        try:
            packet = self.active(conversation).packet
            path = Path(packet.image).resolve()
            if not packet.image or not path.is_relative_to(
                    (self.project / "data/acceptance/media").resolve()):
                raise ValueError("当前样本没有已验证的本地图片")
            os.startfile(path)
        except (ValueError, OSError) as exc:
            messagebox.showerror("图片预览", str(exc))

    def add_packet(self, packet):
        if packet.key in self.cases or self.store.handled(packet.key):
            self.status.set("该消息已有本地验收记录，不重复处理；请发送新的测试消息")
            return
        case = ReviewCase(packet)
        self.store.save(case)
        self.mount_case(case)

    def mount_case(self, case):
        packet = case.packet
        self.cases[packet.key] = case
        tab = self.tabs[packet.conversation]
        tab["keys"].append(packet.key)
        values = list(tab["picker"]["values"])
        values.append(f"{packet.timestamp} · {packet.kind} · {packet.sender}")
        tab["picker"]["values"] = values
        if tab["picker"].current() < 0:
            tab["picker"].current(len(values) - 1)
            self.display(case)

    def display(self, case):
        tab = self.tabs[case.packet.conversation]
        if case.packet.key in tab["keys"]:
            tab["picker"].current(tab["keys"].index(case.packet.key))
        tab["status"].set(f"阶段：{case.phase}　A通过：{case.a_pass}　B通过：{case.b_pass}　"
                          f"C通过：{case.c_pass}")
        timestamp = datetime.fromtimestamp(case.packet.timestamp, UTC).astimezone().isoformat()
        values = [f"发送者：{case.packet.sender}\n时间：{timestamp}\n"
                  f"类型：{case.packet.kind}\n\n{case.packet.text}\n\n{case.packet.note}\n"
                  f"\n原生消息键：{case.packet.key}", case.reply or "尚未调用模型",
                  case.evidence or "未输入、未发送"]
        if case.reply:
            values[1] = (f"模型：{self.settings.model_name}\n"
                         f"独立会话：{case.packet.session}\n"
                         f"本次输入：{case.packet.text or '[已展示的图片]'}\n\n"
                         f"模型实际回复：\n{case.reply}")
        for text, value in zip(tab["texts"], values, strict=True):
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("end", value)
            text.configure(state="disabled")
        tab["image"].configure(image="", text="")
        if case.packet.image:
            with Image.open(case.packet.image) as image:
                image.thumbnail((380, 170))
                photo = ImageTk.PhotoImage(image)
            tab["photo"] = photo
            tab["image"].configure(image=photo)

    def latest(self, conversation, kind=None):
        if self.busy or not self.connected or conversation not in self.backend.targets:
            return
        self.submit(lambda: self.backend.latest(conversation, kind), self.add_packet)

    def poll(self):
        if self.closed:
            return
        if self.connected and not self.busy:
            self.submit(self.backend.poll, lambda packets: [self.add_packet(p) for p in packets])
        self.root.after(2000, self.poll)

    def action(self, conversation, action):
        try:
            if self.busy:
                raise ValueError("当前操作尚未完成")
            if not action.startswith("approve_") and (
                    not self.connected or conversation not in self.backend.targets):
                raise ValueError("当前未连接该白名单窗口或操作尚未完成")
            case = self.active(conversation)
            if action == "reload":
                case.require("a_review")
                self.submit(lambda: self.backend.reload(conversation, case.packet.key),
                            lambda packet: self.reloaded(case, packet))
                return
            if action.startswith("approve_"):
                if messagebox.askyesno("人工验收", "已核对当前显示结果，并判定本阶段通过？"):
                    getattr(case, action)()
                    self.store.save(case)
                    self.display(case)
                return
            if action == "model":
                case.begin_model()
                history = self.store.history(case.packet.session, self.settings.context_turns)

                async def request():
                    self.backend.gate.read(self.backend.targets[conversation])
                    model = HttpModel(self.settings)
                    try:
                        prompt = case.packet.text or "请理解这张图片并回复。"
                        reply = await model.reply(prompt, history,
                                                  images=[Path(case.packet.image)]
                                                  if case.packet.image else None)
                        if len(reply) > self.settings.max_reply_chars:
                            raise ValueError("模型回复超过发送长度上限，请调整模型配置")
                        return reply
                    finally:
                        await model.close()

                self.store.save(case)
                self.submit(lambda: asyncio.run(request()), lambda result:
                            self.finish(case, "model_result", result), case)
            elif action == "draft":
                case.begin_draft()
                self.store.save(case)
                self.submit(lambda: self.backend.draft(conversation, case.reply), lambda result:
                            self.drafted(case, result), case)
            elif action == "send":
                case.require("draft_review")
                if not messagebox.askyesno("发送到微信", f"将已审阅的回复发送给当前白名单聊天？\n"
                                          f"会话：{conversation}\n\n{case.reply}"):
                    return
                case.begin_send()
                self.store.save(case)
                self.submit(lambda: self.backend.send(conversation, case.reply), lambda result:
                            self.finish(case, "sent", result), case)
            self.display(case)
        except Exception as exc:
            messagebox.showerror("本步骤未执行", str(exc))

    def finish(self, case, method, result):
        getattr(case, method)(result)
        self.store.save(case)
        self.display(case)

    def reloaded(self, case, packet):
        case.packet = packet
        self.store.save(case)
        self.display(case)

    def drafted(self, case, evidence):
        case.drafted()
        case.evidence = evidence
        self.store.save(case)
        self.display(case)

    def reject(self, conversation):
        if self.busy:
            self.stop()
            return
        try:
            case = self.active(conversation)
            case.phase = "rejected"
            self.store.save(case)
            self.display(case)
        except ValueError:
            pass
        self.stop()

    def stop(self):
        self.connected = False
        self.epoch += 1
        if self.backend:
            self.backend.stop()
        for case in self.cases.values():
            if case.phase in ("a_review", "a_pass", "b_review", "b_pass", "draft_review"):
                case.phase = "canceled"
                self.store.save(case)
                self.display(case)
        self.windows.configure(state="normal")
        self.status.set("已请求停止 · 后续操作被禁止；等待已开始的任务结束")

    def export(self):
        path = self.project / "data/acceptance" / (
            "review-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
        count = self.store.export(path)
        messagebox.showinfo("本地验收报告", f"已导出 {count} 条实际审阅记录。\n{path}\n"
                            "包含聊天与回复，仅保存在本地。")

    def close(self):
        self.stop()
        if self.busy:
            self.root.after(200, self.close)
            return
        if self.backend:
            self.pool.submit(self.backend.close).result(timeout=10)
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.store.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    with InstanceLock(args.project / "data/control/session.lock"):
        root = tk.Tk()
        AcceptancePanel(root, args.project)
        root.mainloop()


if __name__ == "__main__":
    main()
