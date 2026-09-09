from types import SimpleNamespace

import pytest

from wechat_bot.adapters.windows_uia import WindowsUIAAdapter


class Node:
    def __init__(self, aid="", kind="Group", name="", cls="", nodes=(), enabled=True,
                 visible=True, invoke=True):
        self.element_info = SimpleNamespace(automation_id=aid, control_type=kind)
        self.name, self.cls, self.nodes = name, cls, list(nodes)
        self.enabled, self.visible = enabled, visible
        self.iface_invoke = object() if invoke else None

    def descendants(self, control_type=None):
        all_nodes = [n for child in self.nodes for n in [child, *child.descendants()]]
        return [n for n in all_nodes
                if control_type is None or n.element_info.control_type == control_type]

    def window_text(self):
        return self.name

    def class_name(self):
        return self.cls

    def is_enabled(self):
        return self.enabled

    def is_visible(self):
        return self.visible


def setup(*buttons):
    adapter = WindowsUIAAdapter.__new__(WindowsUIAAdapter)
    chat = {"send_button_id": "", "send_scope_id": "page",
            "send_button_name": "发送", "send_button_class": "mmui::XOutlineButton"}
    return adapter, Node(nodes=[Node("page", nodes=buttons)]), chat


def button(**kwargs):
    return Node(kind="Button", name="发送", cls="mmui::XOutlineButton", **kwargs)


def test_real_wrapper_signature_and_scoped_selector():
    target = button()
    adapter, root, chat = setup(target)
    root.nodes.append(button())  # same label outside configured page must not match
    assert adapter._send_button(root, chat, actionable=True) is target


def test_hidden_duplicate_rejected():
    adapter, root, chat = setup(button(), button(visible=False))
    with pytest.raises(ValueError, match="不唯一"):
        adapter._send_button(root, chat)


@pytest.mark.parametrize("changes", [{"enabled": False}, {"visible": False}])
def test_disabled_or_hidden_button_can_be_inspected_but_never_sent(changes):
    adapter, root, chat = setup(button(**changes))
    assert adapter._send_button(root, chat)
    with pytest.raises(ValueError, match="未启用"):
        adapter._send_button(root, chat, actionable=True)


def test_missing_invoke_is_rejected_without_fallback():
    adapter, root, chat = setup(button(invoke=False))
    with pytest.raises(ValueError, match="Invoke"):
        adapter._send_button(root, chat)


def test_configured_missing_id_never_falls_back_to_name():
    adapter, root, chat = setup(button())
    chat["send_button_id"] = "missing"
    with pytest.raises(ValueError, match="不唯一"):
        adapter._send_button(root, chat)


def test_name_class_and_scope_are_required():
    adapter, root, chat = setup(button())
    for field in ("send_button_name", "send_button_class", "send_scope_id"):
        with pytest.raises(ValueError):
            adapter._send_button(root, {**chat, field: ""})


def test_matching_name_on_wrong_class_is_rejected():
    adapter, root, chat = setup(Node(kind="Button", name="发送", cls="other"))
    with pytest.raises(ValueError, match="不唯一"):
        adapter._send_button(root, chat)


def test_no_cached_element_used_after_replacement():
    old = button()
    adapter, root, chat = setup(old)
    assert adapter._send_button(root, chat) is old
    new = button()
    root.nodes[0].nodes = [new]
    assert adapter._send_button(root, chat) is new
