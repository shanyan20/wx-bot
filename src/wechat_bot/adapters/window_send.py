"""One bounded button click delivered to a pinned HWND, never global mouse/keyboard input."""


def client_point(rect, client_rect, to_client):
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        raise ValueError("发送按钮没有有效区域")
    x1, y1 = to_client((left, top))
    x2, y2 = to_client((right - 1, bottom - 1))
    cl, ct, cr, cb = client_rect
    if not (cl <= x1 <= x2 < cr and ct <= y1 <= y2 < cb):
        raise ValueError("发送按钮不在目标窗口客户区内")
    x, y = to_client(((left + right) // 2, (top + bottom) // 2))
    if not 0 <= x <= 32767 or not 0 <= y <= 32767:
        raise ValueError("按钮客户区坐标超出消息协议范围")
    return x, y


def dispatch_click(hwnd, point, send_message):
    x, y = point
    packed = (y << 16) | x
    # Once dispatch begins, even a timeout is uncertain. Never retry or fall back to Invoke.
    try:
        send_message(hwnd, 0x0201, 0x0001, packed)  # WM_LBUTTONDOWN, MK_LBUTTON
    finally:
        send_message(hwnd, 0x0202, 0, packed)  # release only the same HWND


def click_send_button(window, button, expected_pid):
    import win32gui
    import win32process

    hwnd = window.handle
    if (not hwnd or not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd)
            or not win32gui.IsWindowVisible(hwnd) or not win32gui.IsWindowEnabled(hwnd)
            or win32process.GetWindowThreadProcessId(hwnd)[1] != expected_pid):
        raise ValueError("发送目标窗口不可用或身份已变化")
    if not button.is_visible() or not button.is_enabled():
        raise ValueError("发送按钮未启用")
    rect = button.rectangle()
    point = client_point((rect.left, rect.top, rect.right, rect.bottom),
                         win32gui.GetClientRect(hwnd),
                         lambda value: win32gui.ScreenToClient(hwnd, value))

    def dispatch(target, message, flags, position):
        # SMTO_BLOCK | SMTO_ABORTIFHUNG; bounded, no desktop-wide pointer movement.
        win32gui.SendMessageTimeout(target, message, flags, position, 0x0003, 1500)
    dispatch_click(hwnd, point, dispatch)
    return {"method": "hwnd_mouse_pair", "client_point": list(point)}
