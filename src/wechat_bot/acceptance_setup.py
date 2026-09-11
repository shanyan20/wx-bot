"""Explicit calibration button implementation. No search, typing, model calls or sends."""

import subprocess
import sys
from pathlib import Path


def calibrate(project, title, check=lambda: None):
    import psutil
    import win32gui
    import win32process

    project = Path(project).resolve()
    matches = []
    def visit(hwnd, _):
        if win32gui.GetWindowText(hwnd) != title:
            return
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if Path(psutil.Process(pid).exe()).name.lower() == "weixin.exe":
            matches.append((hwnd, pid))
    win32gui.EnumWindows(visit, None)
    if len(matches) != 1:
        raise ValueError("请先人工打开该联系人的独立聊天窗口；标题必须唯一")
    _, pid = matches[0]
    interpreter = project / ".conda/python.exe"
    python = str(interpreter) if interpreter.exists() else sys.executable
    preflight = project / "data/acceptance/gate-preflight.json"
    activated = project / "data/acceptance/gate-activation.json"
    commands = [
        ["accessibility_gate.py", "--pid", str(pid), "--title", title,
         "--output", str(preflight)],
        ["accessibility_gate.py", "--pid", str(pid), "--title", title,
         "--apply-report", str(preflight), "--output", str(activated)],
        ["db_key_probe.py", "--pid", str(pid), "--title", title],
    ]
    for script, *args in commands:
        check()
        result = subprocess.run([python, str(project / "scripts" / script), *args],
                                cwd=project, capture_output=True, timeout=180,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        check()
        if result.returncode:
            raise ValueError(f"{script} 校准未通过；未启动 Bot，请检查独立窗口/版本")
