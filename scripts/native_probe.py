"""Read-only comparison of UIA backends for one exact window and process.

No WeChat() initializer, contact search, keyboard, clipboard, process-memory or DB access.
Each backend runs in a disposable subprocess with a timeout. No chat text is output.
"""

import argparse
import json
import os
import subprocess
import sys
from collections import deque
from pathlib import Path


def one_window(title, pid):
    import win32gui
    import win32process
    matches = []

    def visit(hwnd, _):
        if (win32gui.GetWindowText(hwnd) == title
                and win32process.GetWindowThreadProcessId(hwnd)[1] == pid):
            matches.append(hwnd)
    win32gui.EnumWindows(visit, None)
    if len(matches) != 1:
        raise ValueError("Exact target missing or ambiguous")
    return matches[0]


def inspect_backend(backend, title, pid, details=False):
    hwnd = one_window(title, pid)
    if backend == "pywinauto":
        from pywinauto import Desktop
        window = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
        properties = lambda node: (node.element_info.control_type,  # noqa: E731
                                   bool(node.element_info.automation_id))
        children = lambda node: node.children()  # noqa: E731
        identity = lambda node: (node.element_info.automation_id,  # noqa: E731
                                node.element_info.name, node.element_info.class_name)
    else:
        import wxauto4
        wxauto4.WxParam.TELEMETRY_ENABLED = False
        wxauto4.WxParam.ENABLE_FILE_LOGGER = False
        from wxauto4 import uia
        # Use the documented low-level reader; do not instantiate the high-level WeChat class.
        window = uia.ControlFromHandle(hwnd)
        properties = lambda node: (node.ControlTypeName, bool(node.AutomationId))  # noqa: E731
        children = lambda node: node.GetChildren()  # noqa: E731
        identity = lambda node: (node.AutomationId, node.Name, node.ClassName)  # noqa: E731
    queue = deque([(window, 0)])
    nodes = []
    depth_limited = False
    while queue and len(nodes) < 200:
        node, depth = queue.popleft()
        kind, has_id = properties(node)
        record = {"depth": depth, "control_type": kind, "has_automation_id": has_id}
        if details:
            aid, name, class_name = identity(node)
            record.update(automation_id=aid, class_name=class_name,
                          name_matches_target=name == title,
                          name_is_send_label=name in ("发送", "发送(S)", "Send"))
        nodes.append(record)
        descendants = children(node)
        if depth < 24:
            queue.extend((child, depth + 1) for child in descendants)
        elif descendants:
            depth_limited = True
    # Recheck identity at the end; the user may have closed or switched the window.
    if one_window(title, pid) != hwnd:
        raise ValueError("Target changed during inspection")
    return {"backend": backend, "status": "inspected", "nodes": nodes,
            "node_count": len(nodes), "possibly_truncated": bool(queue) or depth_limited,
            "read_only": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--backend", choices=("pywinauto", "wxauto4"))
    parser.add_argument("--details", action="store_true",
                        help="Include IDs/classes, never arbitrary control names or message text")
    parser.add_argument("--output", type=Path, default=Path("data/native-probe.json"))
    args = parser.parse_args()
    if args.backend:
        try:
            result = inspect_backend(args.backend, args.title, args.pid, args.details)
        except Exception as exc:
            result = {"backend": args.backend, "status": "error",
                      "error_type": type(exc).__name__, "read_only": True}
        print(json.dumps(result, ensure_ascii=False))
        return
    results = []
    for backend in ("pywinauto", "wxauto4"):
        try:
            child = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--title", args.title,
                 "--pid", str(args.pid), "--backend", backend,
                 *(["--details"] if args.details else [])],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            result = json.loads(child.stdout.strip().splitlines()[-1])
        except subprocess.TimeoutExpired:
            result = {"backend": backend, "status": "timeout", "read_only": True}
        except (ValueError, IndexError):
            result = {"backend": backend, "status": "unreadable_result", "read_only": True}
        results.append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
