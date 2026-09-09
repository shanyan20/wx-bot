"""Local GUI smoke: no WeChat access; skip headless platforms without a display."""

import os
import tkinter as tk
from dataclasses import replace

import pytest

from wechat_bot.panel import Panel


@pytest.mark.skipif(os.name != "nt" and not os.environ.get("DISPLAY"), reason="No GUI display")
def test_panel_starts_off_and_persists_selection_without_starting(settings, tmp_path):
    root = tk.Tk()
    root.withdraw()
    try:
        settings = replace(settings, adapter="windows_uia", windows={"verified": False, "chats": [
            {"id": "test", "chat_type": "private", "window_title": "Synthetic test contact"},
        ]})
        panel = Panel(root, settings, tmp_path)
        root.update_idletasks()
        assert not panel.controller.busy
        assert "已关闭" in panel.status.get()
        panel.table.selection_set("0")
        panel.toggle()
        assert panel.selected == {("private", "test")}
        assert not panel.controller.busy
        assert (tmp_path / "allowlist.json").exists()
        panel.remove()
        assert panel.selected == set()
        assert panel.chats == []
    finally:
        root.destroy()
