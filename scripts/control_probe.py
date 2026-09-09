"""Read-only inspection of send selectors and message identity metadata."""

import argparse
import hashlib
import json
from pathlib import Path

from native_probe import one_window


def probe(title, pid):
    from pywinauto import Desktop

    hwnd = one_window(title, pid)
    window = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    buttons = []
    for node in window.descendants(control_type="Button"):
        if node.window_text() in ("发送", "发送(S)", "Send"):
            try:
                invoke = node.iface_invoke is not None
            except Exception:
                invoke = False
            parent = node.parent()
            buttons.append({"name": node.window_text(), "class_name": node.class_name(),
                            "automation_id": node.element_info.automation_id,
                            "parent_class": parent.class_name(),
                            "parent_id": parent.element_info.automation_id,
                            "invoke_available": invoke, "enabled": node.is_enabled(),
                            "visible": node.is_visible()})
    lists = [n for n in window.descendants(control_type="List")
             if n.element_info.automation_id == "chat_message_list"]
    if len(lists) != 1:
        raise ValueError("Message list missing or ambiguous")
    rows = []
    for row in lists[0].children(control_type="ListItem"):
        metadata = {}
        for label, prop in (("help", 30013), ("item_type", 30021), ("item_status", 30026),
                            ("aria_role", 30101), ("aria_properties", 30102)):
            value = row.element_info.element.GetCurrentPropertyValue(prop)
            metadata[label] = str(value) if value else ""
        rows.append({"class_name": row.class_name(),
                     "automation_id": row.element_info.automation_id,
                     "runtime_id": list(row.element_info.runtime_id), "metadata": metadata,
                     "name_length": len(row.window_text()),
                     "name_sha256": hashlib.sha256(row.window_text().encode()).hexdigest(),
                     "children": [{"class_name": n.class_name(),
                                   "automation_id": n.element_info.automation_id,
                                   "control_type": n.element_info.control_type}
                                  for n in row.descendants()]})
    if one_window(title, pid) != hwnd:
        raise ValueError("Window changed")
    return {"send_buttons": buttons, "rows": rows, "read_only": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.title, args.pid)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
