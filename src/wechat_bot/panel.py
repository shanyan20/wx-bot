"""Local Tk control panel. Starts OFF; GUI state never persists an enabled flag."""

import argparse
import json
import queue
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from wechat_bot.config import load_settings
from wechat_bot.control import Controller, load_selection, save_selection
from wechat_bot.locking import InstanceLock

FIELDS = {
    "id": "会话唯一 ID", "window_title": "独立窗口精确标题",
    "process_id": "微信进程 PID", "header_id": "会话头 AutomationId",
    "header_text": "会话头精确身份文本", "list_id": "消息列表 AutomationId",
    "input_id": "输入框 AutomationId", "send_button_id": "发送按钮 AutomationId",
    "send_button_name": "无 ID 按钮：精确名称", "send_button_class": "无 ID 按钮：精确类名",
    "send_scope_id": "无 ID 按钮：容器 AutomationId",
    "sender_id": "消息发送者 AutomationId", "body_id": "消息正文 AutomationId",
    "timestamp_id": "消息时间 AutomationId", "self_sender": "本账号稳定发送者 ID",
}


class Panel:
    def __init__(self, root, settings, data_dir):
        self.root, self.settings, self.data_dir = root, settings, Path(data_dir)
        self.events = queue.Queue()
        self.controller = Controller(
            settings, data_dir, lambda kind, text: self.events.put((kind, text)))
        self.closing = False
        self.last_error = ""
        self.controls = []
        self.profile_path = self.data_dir / "bindings.json"
        self.selection_path = self.data_dir / "allowlist.json"
        self.chats = list(settings.windows.get("chats", []))
        if self.profile_path.exists():
            self.chats = json.loads(self.profile_path.read_text(encoding="utf-8"))
        selected = load_selection(self.selection_path)
        self.selected = set(selected)
        root.title("微信 Bot · 测试控制台")
        root.geometry("1020x730")
        root.minsize(920, 640)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=8)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("State.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="个人微信测试控制台", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="先绑定独立聊天窗口，再选择白名单。每次打开均为关闭状态。") \
            .pack(anchor="w", pady=(8, 18))
        self.status = tk.StringVar(value="● Bot 已关闭")
        ttk.Label(outer, textvariable=self.status, style="State.TLabel").pack(anchor="w")
        self.detail = tk.StringVar(value="尚未连接微信；不会读取或回复任何聊天。")
        ttk.Label(outer, textvariable=self.detail, wraplength=950).pack(anchor="w", pady=(6, 12))
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 18))
        self.button(actions, "只读检查", lambda: self.start(True))
        self.button(actions, "开启 Bot", lambda: self.start(False))
        self.stop_button = ttk.Button(actions, text="■ 关闭 Bot / 紧急停止", command=self.stop)
        self.stop_button.pack(side="left", padx=(0, 10))
        self.echo = tk.BooleanVar(value=True)
        echo_button = ttk.Checkbutton(actions, text="Echo 测试（不调用模型）", variable=self.echo)
        echo_button.pack(side="right")
        self.controls.append(echo_button)
        ttk.Label(outer, text="允许 Bot 处理的联系人 / 群聊", style="State.TLabel") \
            .pack(anchor="w", pady=(0, 8))
        self.table = ttk.Treeview(
            outer, columns=("allowed", "type", "title", "id", "verified"),
            show="headings", height=8, selectmode="browse",
        )
        for key, title, width in [("allowed", "白名单", 70), ("type", "类型", 70),
                                  ("title", "测试窗口", 270), ("id", "会话 ID", 180),
                                  ("verified", "绑定状态", 150)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", lambda _: self.toggle())
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=10)
        self.button(toolbar, "加入 / 移出白名单", self.toggle)
        self.button(toolbar, "新增绑定", lambda: self.edit(None))
        self.button(toolbar, "编辑绑定", self.edit_selected)
        self.button(toolbar, "删除绑定", self.remove)
        self.button(toolbar, "导入 TOML 绑定", self.import_profile)
        ttk.Label(outer, text=f"群聊命令前缀：{settings.group_prefix!r}。"
                  "不会搜索联系人、切换聊天或抢占键盘焦点。",
                  wraplength=950).pack(anchor="w")
        ttk.Label(outer, text="停止会阻止后续写入，已经调用的微信发送无法撤回。"
                  "出现故障自动关闭，原队列不在下次启动时重放。", wraplength=950) \
            .pack(anchor="w", pady=(4, 12))
        self.counts = tk.StringVar(value="本次任务：尚未开始")
        ttk.Label(outer, textvariable=self.counts).pack(anchor="w")
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.drain)

    def button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side="left", padx=(0, 8))
        self.controls.append(button)

    def refresh(self):
        selected_rows = self.table.selection()
        self.table.delete(*self.table.get_children())
        for index, chat in enumerate(self.chats):
            key = (chat.get("chat_type"), chat.get("id"))
            self.table.insert("", "end", iid=str(index), values=(
                "✓ 允许" if key in self.selected else "— 禁用",
                "群聊" if key[0] == "group" else "联系人",
                chat.get("window_title", ""), key[1],
                "已人工校准" if chat.get("verified", self.settings.windows.get("verified"))
                is True else "待校准 · 不可开启",
            ))
        if selected_rows and self.table.exists(selected_rows[0]):
            self.table.selection_set(selected_rows[0])

    def persist(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.profile_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.chats, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.profile_path)
        save_selection(self.selection_path, self.selected)
        self.refresh()

    def index(self):
        rows = self.table.selection()
        return int(rows[0]) if rows else None

    def toggle(self):
        index = self.index()
        if self.controller.busy or index is None:
            return
        chat = self.chats[index]
        key = (chat["chat_type"], chat["id"])
        self.selected.symmetric_difference_update({key})
        self.persist()

    def remove(self):
        index = self.index()
        if self.controller.busy or index is None:
            return
        chat = self.chats.pop(index)
        self.selected.discard((chat["chat_type"], chat["id"]))
        self.persist()

    def edit_selected(self):
        index = self.index()
        if index is not None:
            self.edit(index)

    def edit(self, index):
        if self.controller.busy:
            return
        chat = self.chats[index] if index is not None else {}
        dialog = tk.Toplevel(self.root)
        dialog.title("绑定独立测试聊天窗口")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        variables = {}
        for row, (key, label) in enumerate(FIELDS.items()):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            variable = tk.StringVar(value=str(chat.get(key, "")))
            ttk.Entry(frame, textvariable=variable, width=55).grid(row=row, column=1, pady=4)
            variables[key] = variable
        kind = tk.StringVar(value=chat.get("chat_type", "private"))
        next_row = len(FIELDS)
        ttk.Label(frame, text="类型 private / group").grid(row=next_row, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=kind, values=("private", "group"), state="readonly") \
            .grid(row=next_row, column=1, sticky="w")
        verified = tk.BooleanVar(value=False)  # editing invalidates earlier calibration
        ttk.Checkbutton(frame, text="已人工核验稳定身份、消息 ID、时间与所有控件",
                        variable=verified) \
            .grid(row=next_row + 1, column=0, columnspan=2, pady=12)
        ttk.Label(frame, text="缺少字段可以先保存待校准；不要用昵称或临时序号冒充稳定 ID。") \
            .grid(row=next_row + 2, column=0, columnspan=2)

        def save():
            if self.controller.busy:
                return
            result = {key: variable.get().strip() for key, variable in variables.items()}
            result.update(chat_type=kind.get(), verified=verified.get())
            try:
                result["process_id"] = int(result["process_id"] or "0")
                if not result["id"] or any(
                    item["id"] == result["id"] for i, item in enumerate(self.chats) if i != index
                ):
                    raise ValueError("请填写不重复的会话 ID")
                if index is None:
                    self.chats.append(result)
                else:
                    self.selected.discard((chat["chat_type"], chat["id"]))
                    self.chats[index] = result
                self.persist()
                dialog.destroy()
            except ValueError as exc:
                messagebox.showerror("无法保存", str(exc), parent=dialog)
        ttk.Button(frame, text="保存绑定（不会开启 Bot）", command=save) \
            .grid(row=next_row + 3, column=0, columnspan=2, pady=12)

    def import_profile(self):
        if self.controller.busy:
            return
        path = filedialog.askopenfilename(filetypes=[("TOML 配置", "*.toml")])
        if not path:
            return
        try:
            settings = load_settings(Path(path))
            if settings.adapter != "windows_uia":
                raise ValueError("请导入 windows_uia 配置")
            self.chats = [dict(chat, verified=False) for chat in settings.windows.get("chats", [])]
            self.selected.clear()
            self.persist()
        except Exception:
            messagebox.showerror("导入失败", "配置不可读取或不是有效 UIA 配置。")

    def start(self, inspect_only):
        try:
            self.last_error = ""
            self.controller.settings = replace(
                self.settings, windows={**self.settings.windows, "chats": self.chats})
            self.controller.launch(self.selected, inspect_only=inspect_only, echo=self.echo.get())
            self.status.set("● 正在检查 · 尚未启用")
            for control in self.controls:
                control.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("未开启", str(exc))

    def stop(self):
        self.controller.stop()
        if self.controller.busy:
            self.status.set("● 正在停止 · 等待已开始的操作结束")
            self.detail.set("已撤销后续写入权限；已调用的发送无法撤回，请等待关闭状态。")

    def close(self):
        self.closing = True
        self.stop()
        if not self.controller.busy:
            self.root.destroy()

    def drain(self):
        while not self.events.empty():
            kind, text = self.events.get_nowait()
            if kind == "counts":
                self.counts.set("本次任务：" + text)
            elif kind == "error":
                self.last_error = text
                self.detail.set(text)
            elif kind == "running":
                self.status.set("● Bot 已开启 · 白名单模式")
                self.detail.set(text)
            elif kind == "stopped":
                self.status.set("● Bot 已关闭")
                if not self.last_error and self.detail.get().startswith("已撤销"):
                    self.detail.set(text)
            else:
                self.detail.set(text)
        if not self.controller.busy:
            for control in self.controls:
                control.configure(state="normal")
            if self.closing:
                self.root.destroy()
                return
        self.root.after(100, self.drain)


def launch(settings, data_dir):
    with InstanceLock(Path(data_dir) / "panel.lock"):
        root = tk.Tk()
        Panel(root, settings, data_dir)
        root.mainloop()


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    local = root / "config/wechat.local.toml"
    parser.add_argument("--config", type=Path,
                        default=local if local.exists() else root / "config/windows.example.toml")
    args = parser.parse_args()
    launch(load_settings(args.config), root / "data/control")


if __name__ == "__main__":
    main()
