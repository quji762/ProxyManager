"""win_max=True restore: the maximize toggle's prev bounds must come from
the SAVED win_rect, not from the fresh window's default size (which used to
be captured and then written back to the config). All Win32 calls are faked
— no real window is touched."""

from unittest.mock import patch

import config_store
import tray_icon
import window_win32
from js_api import Api

DEFAULT_RECT = (0, 0, 1060, 700)
WORKAREA = (0, 0, 1920, 1080)
SAVED = [100, 80, 700, 520]  # last session's normal bounds, 600x440


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def _patch_user32(monkeypatch, swp):
    user32 = window_win32.user32

    def get_rect(hwnd, ref):
        r = ref._obj
        r.left, r.top, r.right, r.bottom = DEFAULT_RECT  # fresh default size
        return 1

    def iconic(hwnd):
        return 0

    def spi(msg, up, ref, ini):
        wa = ref._obj
        wa.left, wa.top, wa.right, wa.bottom = WORKAREA
        return 1

    def set_pos(hwnd, after, x, y, w, h, flags):
        swp.append((x, y, x + w, y + h))
        return 1

    monkeypatch.setattr(user32, "GetWindowRect", get_rect)
    monkeypatch.setattr(user32, "IsIconic", iconic)
    monkeypatch.setattr(user32, "SystemParametersInfoW", spi)
    monkeypatch.setattr(user32, "SetWindowPos", set_pos)
    monkeypatch.setattr("js_api._our_hwnds", lambda: [1234])


def _primed_api(tmp_path, monkeypatch, swp):
    api = _make_api(tmp_path, monkeypatch)
    api._window = object()  # non-None: _restore_window_geometry requires it
    api._win_max = True
    api._win_rect = list(SAVED)
    _patch_user32(monkeypatch, swp)
    monkeypatch.setattr(tray_icon, "_geometry_restored", False)
    return api


def test_win_max_restore_keeps_saved_bounds(tmp_path, monkeypatch):
    swp = []
    api = _primed_api(tmp_path, monkeypatch, swp)

    tray_icon._restore_window_geometry(api)

    # The seed: prev bounds are the SAVED rect, not the default size.
    assert api._prev_bounds == tuple(SAVED)
    assert api._maximized is True
    # ...and the persisted geometry must stay the saved rect too (it used to
    # be overwritten with the fresh window's default size here).
    cfg = config_store.load_config()
    assert cfg["win_rect"] == SAVED
    assert cfg["win_max"] is True

    # User clicks "restore": the window must land on the saved rect.
    api._toggle_maximize()
    assert swp[-1] == tuple(SAVED)
    assert api._maximized is False


def test_win_max_restore_without_saved_rect_keeps_default_capture(
        tmp_path, monkeypatch):
    swp = []
    api = _primed_api(tmp_path, monkeypatch, swp)
    api._win_rect = None

    tray_icon._restore_window_geometry(api)

    # Nothing saved to seed with: capture the (default) current bounds.
    assert api._prev_bounds == DEFAULT_RECT
    assert api._maximized is True


def test_offscreen_rect_is_clamped_to_primary(tmp_path, monkeypatch):
    """A saved rect on a disconnected monitor must not restore off-screen."""
    from window_win32 import _clamp_rect_visible
    user32 = window_win32.user32

    def no_monitor(ref, flags):
        return 0

    def spi(msg, up, ref, ini):
        wa = ref._obj
        wa.left, wa.top, wa.right, wa.bottom = WORKAREA
        return 1

    monkeypatch.setattr(user32, "MonitorFromRect", no_monitor)
    monkeypatch.setattr(user32, "SystemParametersInfoW", spi)
    out = _clamp_rect_visible(8000, 8000, 8600, 8440)
    assert out[0] == WORKAREA[0]
    assert out[1] == WORKAREA[1]
    assert (out[2] - out[0]) == 600
    assert (out[3] - out[1]) == 440


def test_normal_restore_positions_window_at_saved_rect(tmp_path, monkeypatch):
    swp = []
    api = _primed_api(tmp_path, monkeypatch, swp)
    api._win_max = False

    tray_icon._restore_window_geometry(api)

    assert swp == [tuple(SAVED)]
    assert api._maximized is False
