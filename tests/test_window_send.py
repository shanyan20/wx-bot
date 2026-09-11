import pytest

from wechat_bot.adapters.window_send import client_point, dispatch_click


def test_screen_rectangle_translates_to_pinned_client_coordinates():
    assert client_point((805, 843, 865, 875), (0, 0, 747, 800),
                        lambda p: (p[0] - 142, p[1] - 97)) == (693, 762)


@pytest.mark.parametrize("rect", [(0, 0, 0, 0), (-1, 20, 30, 40), (20, 20, 500, 500)])
def test_invalid_or_outside_button_never_produces_a_click(rect):
    with pytest.raises(ValueError):
        client_point(rect, (0, 0, 100, 100), lambda p: p)


def test_exactly_one_mouse_pair_to_same_hwnd():
    calls = []
    dispatch_click(123, (12, 34), lambda *args: calls.append(args))
    assert calls == [(123, 0x0201, 1, (34 << 16) | 12),
                     (123, 0x0202, 0, (34 << 16) | 12)]


def test_down_timeout_releases_same_target_but_does_not_retry_press():
    calls = []
    def dispatch(*args):
        calls.append(args)
        if args[1] == 0x0201:
            raise TimeoutError("possibly delivered")
    with pytest.raises(TimeoutError):
        dispatch_click(123, (12, 34), dispatch)
    assert [c[1] for c in calls] == [0x0201, 0x0202]
    assert {c[0] for c in calls} == {123}
