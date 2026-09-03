"""
ProxyManager - pywebview (WebView2) front-end.

The UI is a zero-build single-page web app (ui/index.html) rendered in
WebView2; every system-touching operation is delegated to the backend
library proxy_manager.py via the js_api bridge. Run:

    py -3.13 app.py        (or double-click start.bat)

Config (config v2): host, port, powershell, wsl, git_win, git_wsl, npm, pip,
npmwsl, pipwsl, pin, mode, ports (quick-select presets), autostart, bg_path,
bg_dim, card_alpha, quit_clear, wsl_all_distros, close_hint_done, restore.
A legacy {"git": bool} key migrates into git_win/git_wsl on load.

Window operations avoid pywebview's set_on_top (it writes WinForms TopMost
from the js_api worker thread and deadlocks the UI - see memory): pin and
maximize go through raw Win32 on the real HWND instead.
"""

import json
import os
import subprocess
import sys
import threading
import time

# ui/ ships as data next to the script; under PyInstaller it lands in the
# extraction dir (sys._MEIPASS) instead.
if getattr(sys, "frozen", False):
    _BASE = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

import webview  # noqa: E402

import proxy_manager as pm  # noqa: E402  (backend: registry/WSL/git logic)

UI_PATH = os.path.join(_BASE, "ui", "index.html")

# Shown in the settings dialog; bump together with the v* release tag.
APP_VERSION = "1.3.0"

# Frameless gives the HypoMux-style integrated title bar. Flip to False if a
# future pywebview build regresses edge-resizing on frameless windows.
FRAMELESS = True

APP_DIR = pm._config_dir()
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
AUTOSTART_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "ProxyManager"

DEFAULT_PORTS = [7890, 7892, 7897]
_MAX_BG_BYTES = 8 * 1024 * 1024  # custom wallpaper cap (data-URL'd into the page)
# Connectivity test: gstatic is often poisoned/blocked in CN; try several.
_PROBE_URLS = (
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://captive.apple.com/hotspot-detect.html",
    "http://connect.rom.miui.com/generate_204",
    "http://www.gstatic.com/generate_204",
)
_START_HIDDEN = any(a in ("--tray", "--hidden") for a in sys.argv[1:])


# ─── Config v2 (superset + legacy migration) ─────────────────────────────────
def load_config():
    defaults = {
        "host": pm.DEFAULT_HOST, "port": pm.DEFAULT_PORT,
        "powershell": True, "wsl": True, "git_win": False, "git_wsl": False,
        "npm": False, "pip": False, "npmwsl": False, "pipwsl": False,
        "pin": False, "mode": "light", "ports": list(DEFAULT_PORTS),
        "autostart": False, "bg_path": "", "bg_dim": 0.45, "card_alpha": 0.92,
        "quit_clear": False, "wsl_all_distros": True, "close_hint_done": False,
        "restore": {},
    }
    cfg = {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}

    # Legacy single Git switch -> split endpoints.
    if "git" in cfg and "git_win" not in cfg:
        cfg["git_win"] = cfg["git_wsl"] = bool(cfg["git"])

    out = dict(defaults)
    for k in ("host", "port", "mode", "bg_path"):
        if cfg.get(k) is not None:
            out[k] = str(cfg[k])
    if not _valid_host(out["host"]):
        out["host"] = defaults["host"]  # hand-edited config; never trust it
    if pm._is_valid_port(out["port"]):
        out["port"] = str(int(out["port"]))
    else:
        out["port"] = defaults["port"]
    for k in ("powershell", "wsl", "git_win", "git_wsl", "npm", "pip",
              "npmwsl", "pipwsl", "pin", "autostart", "quit_clear",
              "wsl_all_distros", "close_hint_done"):
        out[k] = bool(cfg.get(k, defaults[k]))
    if out["mode"] not in ("light", "dark"):
        out["mode"] = "light"

    ports = cfg.get("ports")
    if isinstance(ports, list):
        seen = []
        for p in ports:
            try:
                pi = int(p)
            except (TypeError, ValueError):
                continue
            if 1 <= pi <= 65535 and pi not in seen:
                seen.append(pi)
        if seen:
            out["ports"] = seen[:12]
    try:
        d = float(cfg.get("bg_dim", defaults["bg_dim"]))
        out["bg_dim"] = min(0.8, max(0.0, d))
    except (TypeError, ValueError):
        pass
    try:
        a = float(cfg.get("card_alpha", defaults["card_alpha"]))
        out["card_alpha"] = min(1.0, max(0.5, a))
    except (TypeError, ValueError):
        pass
    try:
        # Pre-2.1 default was 5 (Mbps = 0.625 MB/s): the spark bar saturated
        # almost immediately while the settings popover speaks MB/s. The
        # untouched default migrates to 40 Mbps (= 5 MB/s); hand-set values
        # (always MB/s × 8 from the old popover) pass through untouched.
        if cfg.get("speed_full") == 5:
            cfg["speed_full"] = 40
        s = float(cfg.get("speed_full", 40))
        out["speed_full"] = round(s, 1) if 0 < s <= 100000 else 40
    except (TypeError, ValueError):
        out["speed_full"] = 40
    rect = cfg.get("win_rect")
    out["win_rect"] = None
    if isinstance(rect, list) and len(rect) == 4:
        try:
            out["win_rect"] = [int(v) for v in rect]
        except (TypeError, ValueError):
            pass
    out["win_max"] = bool(cfg.get("win_max", False))
    restore = cfg.get("restore")
    out["restore"] = restore if isinstance(restore, dict) else {}
    return out


def save_config(cfg):
    """Write the full superset; unknown keys from future versions survive.
    Returns True on success, False if the write failed."""
    with _cfg_lock:
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            cur = {}
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                if not isinstance(cur, dict):
                    cur = {}
            except Exception:
                pass
            cur.update({k: cfg[k] for k in (
                "host", "port", "powershell", "wsl", "git_win", "git_wsl",
                "npm", "pip", "npmwsl", "pipwsl", "pin", "mode", "ports",
                "autostart", "bg_path", "bg_dim", "card_alpha", "speed_full",
                "quit_clear", "wsl_all_distros", "close_hint_done", "restore",
                "win_rect", "win_max")
                if k in cfg})
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2, ensure_ascii=False)
            return True
        except OSError:
            return False


_cfg_lock = threading.RLock()  # reentrant: Api.save_config nests into save_config


def _valid_host(h):
    return bool(h) and not any(c.isspace() for c in h) \
        and all(c in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in h) \
        and "://" not in h and ":" not in h


# ─── Autostart (HKCU Run key) ────────────────────────────────────────────────
def _autostart_command():
    # --tray: start hidden in the notification area so login isn't a popup.
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile: __file__ is a per-run temp path that would
        # never match after extraction; the exe path is stable.
        return f'"{sys.executable}" --tray'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}" --tray'


def autostart_enabled():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN, 0,
                             winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            winreg.CloseKey(key)
            val = str(v)
            return str(_autostart_command()) in val or (
                getattr(sys, "frozen", False) and sys.executable in val) or (
                os.path.abspath(__file__) in val)
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except OSError:
        return False


def autostart_set(on):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN, 0,
                             winreg.KEY_SET_VALUE)
        if on:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                              _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except OSError as e:
        return str(e)


# ─── Win32 window helpers (thread-safe from js_api workers) ──────────────────
import ctypes  # noqa: E402

user32 = ctypes.WinDLL("user32")


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


# Explicit prototypes: without argtypes ctypes truncates handles/pointers to
# 32 bits, which made SetWindowPos(HWND_TOPMOST, ...) silently fail (ret=0).
user32.SetWindowPos.argtypes = [
    ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [ctypes.c_ssize_t, ctypes.POINTER(_RECT)]
user32.GetWindowRect.restype = ctypes.c_int
user32.GetClientRect.argtypes = [ctypes.c_ssize_t, ctypes.POINTER(_RECT)]
user32.GetClientRect.restype = ctypes.c_int
_ENUMCHILD = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ssize_t,
                                ctypes.c_ssize_t)
user32.EnumChildWindows.argtypes = [ctypes.c_ssize_t, _ENUMCHILD,
                                    ctypes.c_ssize_t]
user32.SystemParametersInfoW.argtypes = [
    ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(_RECT), ctypes.c_uint]
user32.SystemParametersInfoW.restype = ctypes.c_int
user32.GetWindowLongW.argtypes = [ctypes.c_ssize_t, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowTextW.argtypes = [ctypes.c_ssize_t, ctypes.c_wchar_p, ctypes.c_int]
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_ssize_t, ctypes.c_ssize_t), ctypes.c_ssize_t]
user32.SendMessageW.argtypes = [
    ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.ReleaseCapture.restype = ctypes.c_int
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [ctypes.c_ssize_t, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_ssize_t, ctypes.c_int, ctypes.c_ssize_t]
user32.CallWindowProcW.restype = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes = [
    ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_uint,
    ctypes.c_ssize_t, ctypes.c_ssize_t]
user32.GetDpiForWindow.restype = ctypes.c_uint
user32.GetDpiForWindow.argtypes = [ctypes.c_ssize_t]
user32.GetDpiForSystem.restype = ctypes.c_uint
user32.GetWindowThreadProcessId.argtypes = [
    ctypes.c_ssize_t, ctypes.POINTER(ctypes.c_uint)]
user32.GetWindowThreadProcessId.restype = ctypes.c_uint
user32.IsIconic.argtypes = [ctypes.c_ssize_t]
user32.IsIconic.restype = ctypes.c_int
user32.ShowWindow.argtypes = [ctypes.c_ssize_t, ctypes.c_int]
user32.ShowWindow.restype = ctypes.c_int
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.c_ssize_t
user32.SetForegroundWindow.argtypes = [ctypes.c_ssize_t]
user32.SetForegroundWindow.restype = ctypes.c_int
user32.AllowSetForegroundWindow.argtypes = [ctypes.c_uint]
user32.AllowSetForegroundWindow.restype = ctypes.c_int
_SW_RESTORE = 9


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
user32.GetCursorPos.restype = ctypes.c_int


class _MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", _POINT), ("ptMaxSize", _POINT),
                ("ptMaxPosition", _POINT), ("ptMinTrackSize", _POINT),
                ("ptMaxTrackSize", _POINT)]


# ─── Native frameless resize (form WndProc subclass) ─────────────────────────
# Intercept WM_NCHITTEST on the form and return edge/corner hit codes when the
# cursor is within the margin: Windows then shows the native resize cursor and
# runs its own modal sizing loop. No JS/IPC involved, so there is no race with
# the mouse button, and the WebView2 child never sees the edge clicks.
_GWL_WNDPROC = -4
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x0201
WM_GETMINMAXINFO = 0x0024
_HT_EDGE = 8  # physical px margin, scaled by DPI at hit-test time

_resize_wndproc_ref = None   # trampoline keepalive
_resize_old_proc = None
_resize_form_hwnd = 0
_resize_installed_hwnd = 0   # hwnd the hook currently lives on
_padded_hwnd = 0             # hwnd whose ring has been applied
_resize_maximized = False
_resize_hook_lock = threading.RLock()
_restore_callback = None   # set by main(): restores fake-maximized bounds
_clear_maximized_cb = None # set by main(): clears the fake-max state after
                           # a native caption drag/snap restored the window
                           # behind our back (instance flag + global + icon)
_page_window_ref = None    # set by main(): pywebview window (UI-thread use only)


def _sync_maximized_icon():
    """Push the maximize/restore icon state to the page from a WndProc
    context. evaluate_js must NOT run on the UI thread (the JS-result
    callback needs the message pump we are currently inside — it deadlocks);
    a short-lived worker thread is the safe caller (pywebview Invoke-marshals
    the script to the UI pump)."""
    w = _page_window_ref
    if w is None:
        return
    state = "true" if _resize_maximized else "false"

    def _push():
        try:
            w.evaluate_js(f"applyMaximized({state})")
        except Exception:
            pass

    threading.Thread(target=_push, daemon=True).start()


def _clear_fake_maximized():
    """Reconcile state after a NATIVE caption drag/snap restored the window
    behind our back (drag off the top edge, Aero snap). Sets the module
    global, resets Api._maximized (the maximize BUTTON reads the instance
    flag — leaving it stale makes the next click restore stale prev bounds
    instead of maximizing), and pushes the icon sync. WndProc context:
    still no evaluate_js here — the sync runs on its worker thread."""
    global _resize_maximized
    _resize_maximized = False
    if _clear_maximized_cb is not None:
        try:
            _clear_maximized_cb()
        except Exception:
            pass
    _sync_maximized_icon()
_MIN_W, _MIN_H = 920, 620    # logical px, same as pywebview min_size
_BORDER_PX = 9               # form-owned ring around the webview (logical px)

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_ssize_t,
                              ctypes.c_uint, ctypes.c_ssize_t,
                              ctypes.c_ssize_t)


def _signed16(v):
    return v - 0x10000 if v >= 0x8000 else v


def _resize_hook_proc(hwnd, msg, wparam, lparam):
    """Form-level WM_NCHITTEST hook. Real mouse input over the padded ring
    (see _apply_window_padding) is hit-tested against the form itself, so the
    hook answers with native edge codes: Windows shows the resize cursor and
    runs its own modal sizing loop — no JS, no IPC, no capture games.

    (Subclassing the WebView2 child windows instead is NOT viable: doing that
    from Python tore the children down and exited the process.)"""
    if msg == WM_NCHITTEST and not _resize_maximized and _resize_form_hwnd:
        x = _signed16(lparam & 0xFFFF)
        y = _signed16((lparam >> 16) & 0xFFFF)
        r = _RECT()
        user32.GetWindowRect(_resize_form_hwnd, ctypes.byref(r))
        if r.left <= x <= r.right and r.top <= y <= r.bottom:
            dpi = user32.GetDpiForWindow(hwnd) or 96
            m = max(6, _HT_EDGE * dpi // 96)
            on_l = x - r.left <= m
            on_r = r.right - x <= m
            on_t = y - r.top <= m
            on_b = r.bottom - y <= m
            if on_t and on_l:
                return 13  # HTTOPLEFT
            if on_t and on_r:
                return 14  # HTTOPRIGHT
            if on_b and on_l:
                return 16  # HTBOTTOMLEFT
            if on_b and on_r:
                return 17  # HTBOTTOMRIGHT
            if on_l:
                return 10  # HTLEFT
            if on_r:
                return 11  # HTRIGHT
            if on_t:
                return 12  # HTTOP
            if on_b:
                return 15  # HTBOTTOM
    elif msg == 0x00A3:  # WM_NCLBUTTONDBLCLK
        # Double-click on the native resize ring: HTTOP owns the top strip
        # (for vertical resize), so without this the title-bar double-click
        # there would do nothing. Both codes toggle maximize/restore.
        if wparam in (2, 12) and _restore_callback is not None:
            try:
                _restore_callback()
                _sync_maximized_icon()
            except Exception:
                pass
            return 0
    elif msg == 0x0112:  # WM_SYSCOMMAND
        # A frameless form (no WS_MINIMIZEBOX/WS_MAXIMIZEBOX) drops the
        # taskbar-button click: SC_MINIMIZE / SC_RESTORE arrive only when
        # those styles (or a system menu entry) exist. Handle them ourselves
        # so clicking the taskbar icon minimizes/restores in every state.
        # SC_MAXIMIZE arrives from the native caption drag (app-region drag
        # + IsNonClientRegionSupportEnabled): Windows snaps the title-bar
        # drag to the top edge / corners and issues maximize commands that
        # a fake-maximized frameless window must route through
        # _toggle_maximize to keep the JS icon state in sync.
        sc = wparam & 0xFFF0
        if sc == 0xF020:  # SC_MINIMIZE
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            return 0
        if sc == 0xF030:  # SC_MAXIMIZE
            if not _resize_maximized and _restore_callback is not None:
                try:
                    _restore_callback()
                    _sync_maximized_icon()
                except Exception:
                    pass
            return 0
        if sc == 0xF120:  # SC_RESTORE
            was_iconic = bool(user32.IsIconic(hwnd))
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            if was_iconic:
                # Restoring from the taskbar: a fake-maximized window must
                # return to its maximized size (work area), NOT collapse to
                # prev bounds — only un-minimize here.
                return 0
            if _resize_maximized and _restore_callback is not None:
                try:
                    _restore_callback()
                    _sync_maximized_icon()
                except Exception:
                    pass
            return 0
    elif msg == 0x0005:  # WM_SIZE
        # A native caption drag can restore a maximized (fake-maximized)
        # window by pulling it off the top edge; Windows resizes it to the
        # dragged bounds without any SC_RESTORE. Reconcile the fake-maximize
        # state so the JS icon doesn't claim "maximized" while it isn't.
        # The bounds guard matters: un-minimizing ALSO arrives as
        # SIZE_RESTORED, and that path must keep the maximized state (the
        # window returns to the work area — bounds still match, so we skip).
        if wparam == 0 and _resize_maximized:  # SIZE_RESTORED
            r = _RECT()
            wa = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
            if (r.left, r.top, r.right, r.bottom) != \
                    (wa.left, wa.top, wa.right, wa.bottom):
                _clear_fake_maximized()
    elif msg == 0x0232:  # WM_EXITSIZEMOVE
        # Dragging the caption of a fake-maximized window: the system move
        # loop just MOVES it (it doesn't know the window is "maximized"), so
        # no WM_SIZE/SC_RESTORE fires and the state would go stale. When the
        # loop ends and the bounds no longer match the work area, the user
        # really dragged it off — collapse the fake-max state. (A plain
        # caption click also enters/exits the loop, but bounds still match.)
        if _resize_maximized and _restore_callback is not None:
            r = _RECT()
            wa = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
            if (r.left, r.top, r.right, r.bottom) != \
                    (wa.left, wa.top, wa.right, wa.bottom):
                _clear_fake_maximized()
    elif msg == WM_GETMINMAXINFO:
        # Enforce the minimum window size for the native resize loop.
        try:
            mmi = _MINMAXINFO.from_address(lparam)
            dpi = user32.GetDpiForWindow(hwnd) or 96
            # Never demand a minimum larger than the work area: a 920x620
            # logical minimum overflows a 1366x768 laptop at 150% DPI.
            wa = _RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
            max_w = max(320, wa.right - wa.left)
            max_h = max(240, wa.bottom - wa.top)
            mmi.ptMinTrackSize.x = min(_MIN_W * dpi // 96, max_w)
            mmi.ptMinTrackSize.y = min(_MIN_H * dpi // 96, max_h)
            return 0
        except Exception:
            pass
    return user32.CallWindowProcW(_resize_old_proc, hwnd, msg, wparam, lparam)


def install_resize_hook(hwnd):
    """Subclass the form's WndProc. Re-entry guarded (loaded fires on its own
    thread and the ctypes calls release the GIL). The guard also checks WHICH
    hwnd the hook lives on: WinForms can recreate the handle, and a stale hook
    on the old hwnd must be re-installed on the new one."""
    global _resize_wndproc_ref, _resize_old_proc, _resize_form_hwnd, \
        _resize_installed_hwnd
    with _resize_hook_lock:
        if _resize_old_proc and _resize_installed_hwnd == hwnd:
            return
        try:
            _resize_form_hwnd = hwnd
            # Borderless forms carry no WS_MINIMIZEBOX, so Windows silently
            # ignores taskbar-button clicks (no SC_MINIMIZE is ever sent).
            # Adding the style bit makes the taskbar minimize/restore work.
            _GWL_STYLE = -16
            WS_MINIMIZEBOX = 0x00020000
            style = user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
            if not style & WS_MINIMIZEBOX:
                user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style | WS_MINIMIZEBOX)
                # SWP_NOSIZE|SWP_NOMOVE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_FRAMECHANGED
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
            _resize_old_proc = user32.GetWindowLongPtrW(hwnd, _GWL_WNDPROC)
            _resize_wndproc_ref = _WNDPROC(_resize_hook_proc)
            if user32.SetWindowLongPtrW(
                    hwnd, _GWL_WNDPROC,
                    ctypes.cast(_resize_wndproc_ref, ctypes.c_void_p).value):
                _resize_installed_hwnd = hwnd
            else:
                _resize_old_proc = None  # allow the watchdog to retry
        except Exception:
            _resize_old_proc = None


def _hook_is_ours(hwnd):
    if not hwnd or not _resize_wndproc_ref:
        return False
    cur = user32.GetWindowLongPtrW(hwnd, _GWL_WNDPROC)
    return cur == ctypes.cast(_resize_wndproc_ref, ctypes.c_void_p).value


def ensure_resize_hook(window=None):
    """(Re)install the resize hook on the current form window and make sure
    the form keeps its padded ring. WinForms can recreate its handle (late
    style changes), which silently drops a previous subclass — the watchdog
    calls this to self-heal."""
    global _resize_form_hwnd, _padded_hwnd
    hwnds = _our_hwnds()
    if not hwnds:
        return False
    hwnd = hwnds[0]
    _resize_form_hwnd = hwnd
    if not _hook_is_ours(hwnd):
        install_resize_hook(hwnd)
    ok = _hook_is_ours(hwnd)
    if ok and window is not None and _padded_hwnd != hwnd:
        if _apply_window_padding(window, _BORDER_PX):
            _padded_hwnd = hwnd
    return ok


def _apply_window_padding(window, px):
    """Set Form.Padding on pywebview's WinForms view, marshaled to the UI
    thread via Control.Invoke (pythonnet) — the same pattern pywebview itself
    uses for minimize/maximize. The form instance is window.native."""
    try:
        form = getattr(window, "native", None)
        if form is None:
            return False
        from System import Type, Func  # noqa: E402  (pythonnet)
        from System.Windows.Forms import Padding  # noqa: E402

        def _set():
            form.Padding = Padding(px)

        if form.InvokeRequired:
            form.Invoke(Func[Type](_set))
        else:
            _set()
        return True
    except Exception as e:
        if os.environ.get("PM_DEBUG"):
            print(f"[pm] padding failed: {e}", flush=True)
        return False


def set_resize_maximized(v):
    global _resize_maximized
    _resize_maximized = bool(v)


def ensure_nonclient_drag(window=None):
    """Enable WebView2's native non-client region support on the embedded
    browser control. With it on, elements styled `app-region: drag` are
    hit-tested as HTCAPTION *inside* the browser: the physical mousedown
    enters the system move loop in the same message, exactly like a real
    Win32 caption (the mechanism IssueCapture gets for free from Tauri's
    native title bar). The JS→IPC→SendMessage injection ("drag_move") is
    inherently racy — Chromium owns the mouse capture, so a press-and-drag
    usually releases before the injected loop starts, which read as "the
    drag does nothing most of the time".

    The two paths are naturally exclusive: presses WebView2 claims as
    non-client never reach the DOM, so the JS mousedown fallback can only
    fire when this setting is not actually in effect. No capability flag
    needs to cross the bridge.

    Idempotent and re-entrant: called from the loaded event and the resize
    watchdog, because WinForms can recreate the form handle at any time and
    a fresh browser control needs the setting re-applied. CoreWebView2 has
    thread affinity, so the access is marshaled to the UI thread via
    form.Invoke (same pattern as _apply_window_padding)."""
    try:
        form = getattr(window, "native", None) if window is not None else None
        if form is None:
            return False
        from System import Type, Func  # noqa: E402  (pythonnet)

        def _set():
            browser = getattr(form, "browser", None)  # EdgeChrome instance
            core = getattr(getattr(browser, "webview", None),
                           "CoreWebView2", None)
            if core is not None and \
                    not core.Settings.IsNonClientRegionSupportEnabled:
                core.Settings.IsNonClientRegionSupportEnabled = True

        if form.InvokeRequired:
            form.Invoke(Func[Type](_set))
        else:
            _set()
        return True
    except Exception as e:
        if os.environ.get("PM_DEBUG"):
            print(f"[pm] nonclient drag: {e}", flush=True)
        return False


def _enum_windows(match):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ssize_t, ctypes.c_ssize_t)
    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, buf, 64)
        pid = ctypes.c_uint()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if buf.value and match(pid.value, buf.value, hwnd):
            found.append(hwnd)
        return 1

    user32.EnumWindows(cb, 0)
    return found


def _our_hwnds(prefix="代理管理器", other_pid=False):
    my_pid = os.getpid()
    return _enum_windows(
        lambda pid, title, h:
            title.startswith(prefix)
            and ((pid != my_pid) if other_pid else (pid == my_pid)))


class Api:
    """JS bridge. Every method returns JSON-serializable data and runs on its
    own thread (pywebview dispatches js_api calls off the UI thread), so the
    slow WSL subprocess calls never block rendering."""

    def __init__(self):
        cfg = load_config()
        self.host = cfg["host"]
        self.port = cfg["port"]
        self._speed_lock = threading.Lock()
        self._speed_prev = pm.get_net_counters()
        self._speed = {"up": "0 B/s", "dn": "0 B/s", "dn_num": 0.0,
                       "iface": pm._primary_iface or "", "alive": None,
                       "port": ""}
        self._window = None
        self._icon = None
        self._pinned = cfg.get("pin", False)
        self._maximized = False
        self._prev_bounds = None
        self._bg_cache = None  # (path, data_url)
        self._win_rect = cfg.get("win_rect")      # last normal bounds (physical px)
        self._win_max = bool(cfg.get("win_max", False))
        # Last URL this tool wrote to each git endpoint, so a disable after
        # the user edited host/port still unsets the old value.
        self._git_managed = {"gitwin": None, "gitwsl": None}
        self._tray_ok = False  # close falls back to quit when no tray exists
        self._restore = dict(cfg.get("restore") or {})
        pm.set_wsl_apply_all(cfg.get("wsl_all_distros", True))
        threading.Thread(target=self._speed_loop, daemon=True).start()

    # ── config ──
    def get_config(self):
        cfg = load_config()
        return {
            "version": APP_VERSION,
            "host": self.host, "port": self.port,
            "mode": cfg.get("mode", "light"), "pin": cfg.get("pin", False),
            "ports": cfg.get("ports", DEFAULT_PORTS),
            "autostart": autostart_enabled(),
            "bg_path": cfg.get("bg_path", ""),
            "bg_dim": cfg.get("bg_dim", 0.45),
            "card_alpha": cfg.get("card_alpha", 0.92),
            "speed_full": cfg.get("speed_full", 40),
            "quit_clear": bool(cfg.get("quit_clear", False)),
            "wsl_all_distros": bool(cfg.get("wsl_all_distros", True)),
            "close_hint_done": bool(cfg.get("close_hint_done", False)),
        }

    def save_config(self, data):
        with _cfg_lock:
            cfg = load_config()
            # Validate host/port server-side: they end up inside wsl bash -c
            # and git commands downstream, and the UI only validates the
            # enable path.
            if "host" in data:
                h = str(data["host"]).strip()
                if _valid_host(h):
                    self.host = h
            if "port" in data:
                p = str(data["port"]).strip()
                if pm._is_valid_port(p):
                    self.port = str(int(p))
            cfg["host"], cfg["port"] = self.host, self.port
            for k in ("powershell", "wsl", "git_win", "git_wsl", "npm", "pip",
                      "npmwsl", "pipwsl", "pin"):
                if k in data:
                    cfg[k] = bool(data[k])
            if "mode" in data:
                cfg["mode"] = str(data["mode"])
            if "ports" in data and isinstance(data["ports"], list):
                seen = []
                for p in data["ports"]:
                    try:
                        pi = int(p)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= pi <= 65535 and pi not in seen:
                        seen.append(pi)
                cfg["ports"] = seen[:12] or DEFAULT_PORTS
            if "autostart" in data:
                cfg["autostart"] = bool(data["autostart"])
            if "bg_path" in data:
                bp = str(data["bg_path"])
                ext = os.path.splitext(bp)[1].lower()
                if not bp:
                    cfg["bg_path"] = ""
                elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif") \
                        and os.path.isfile(bp):
                    cfg["bg_path"] = bp
                # else: keep the previous value instead of persisting a
                # bogus/missing path that get_background would choke on.
            if "bg_dim" in data:
                try:
                    cfg["bg_dim"] = min(0.8, max(0.0, float(data["bg_dim"])))
                except (TypeError, ValueError):
                    pass
            if "card_alpha" in data:
                try:
                    cfg["card_alpha"] = min(1.0, max(0.5, float(data["card_alpha"])))
                except (TypeError, ValueError):
                    pass
            if "speed_full" in data:
                try:
                    v = float(data["speed_full"])
                    if 0 < v <= 100000:
                        cfg["speed_full"] = round(v, 1)
                except (TypeError, ValueError):
                    pass
            if "quit_clear" in data:
                cfg["quit_clear"] = bool(data["quit_clear"])
            if "wsl_all_distros" in data:
                cfg["wsl_all_distros"] = bool(data["wsl_all_distros"])
                pm.set_wsl_apply_all(cfg["wsl_all_distros"])
            if "close_hint_done" in data:
                cfg["close_hint_done"] = bool(data["close_hint_done"])
            if "restore" in data and isinstance(data["restore"], dict):
                cfg["restore"] = data["restore"]
                self._restore = dict(data["restore"])
            if "win_rect" in data and isinstance(data["win_rect"], list) \
                    and len(data["win_rect"]) == 4:
                try:
                    rect = [int(v) for v in data["win_rect"]]
                    cfg["win_rect"] = rect
                    self._win_rect = rect
                except (TypeError, ValueError):
                    pass
            if "win_max" in data:
                cfg["win_max"] = bool(data["win_max"])
                self._win_max = bool(data["win_max"])
            cfg["restore"] = self._restore
            if not save_config(cfg):
                return False
        return True

    # ── status refresh: one call per slice, fired in parallel by the UI ──
    def refresh_item(self, which):
        if which == "ps":
            return {"status": pm.get_ps_status(),
                    "vars": pm.get_ps_proxy_vars()}
        if which == "sys":
            return pm.get_sys_proxy()
        if which == "wsl":
            ip, block, wgit = pm.get_wsl_ip_and_block()
            return {"ip": ip, "on": bool(pm.get_wsl_status(block)),
                    "vars": pm.get_wsl_proxy_vars(block), "git": wgit}
        if which == "gitwin":
            return pm.get_git_proxy_win()
        if which == "npm":
            return pm.get_npm_proxy()
        if which == "pip":
            return pm.get_pip_proxy()
        if which == "npmwsl":
            return pm.get_npm_proxy_wsl()
        if which == "pipwsl":
            return pm.get_pip_proxy_wsl()
        if which == "ip":
            return pm.get_local_ip()
        if which == "mode":
            return pm.get_wsl_network_mode()
        return None

    # ── toggle flow: JS orchestrates confirm dialogs, Python applies ──
    def _endpoint_addr(self, host, port):
        """Validated (host, port): explicit JS-passed values win over the
        cached self.host/self.port, so a toggle right after editing the
        fields uses the NEW address (persistSoon only debounces at 400ms)."""
        h = str(host).strip() if host else self.host
        p = str(port).strip() if port else self.port
        if not _valid_host(h) or not pm._is_valid_port(p):
            return None
        return h, str(int(p))

    def prepare_toggle(self, target, on, host=None, port=None):
        """Pre-flight checks for enabling. Returns {ok} | {confirm, message}
        | {error} so the UI can show an in-page dialog before anything is
        applied (replaces the tkinter messagebox flow)."""
        if not on:
            return {"ok": True}
        addr = self._endpoint_addr(host, port)
        if not addr:
            return {"error": "代理地址或端口不合法，请先修正"}
        host, port = addr
        if not pm.proxy_port_alive(host, port):
            return {"confirm": "port",
                    "message": f"{host}:{port} 上没有程序在监听（代理软件可能没有运行）。\n"
                               "继续启用会导致终端联网失败。\n仍要继续吗？"}
        if target in ("wsl", "gitwsl", "npmwsl", "pipwsl", "all") \
                and not pm.wsl_available():
            # Fail before any write attempt: a broken WSL would otherwise sit
            # through the full 15s subprocess timeout inside set_wsl_proxy.
            return {"error": "未检测到可用的 WSL 发行版"}
        if target in ("wsl", "all"):
            shell = pm.wsl_default_shell()
            if shell and shell not in ("bash", "zsh", "fish", "sh"):
                return {"confirm": "shell",
                        "message": f"WSL 默认 shell 是 {shell}，本工具写入 ~/.bashrc、"
                                   "~/.zshrc 和 fish conf.d。\n仍要继续写入吗？"}
        if target in ("gitwin", "all"):
            import shutil
            if not shutil.which("git"):
                return {"error": "未找到 git，请确认已安装 Git"}
        if target == "npm":
            # The config-file write itself never needs the npm CLI, but if
            # Node.js isn't even on PATH the endpoint is probably pointless —
            # ask instead of failing (pip is skipped: `py -m pip` etc. can't
            # be detected reliably and the file is always honored).
            import shutil
            if not shutil.which("npm"):
                return {"confirm": "npm",
                        "message": "PATH 上未检测到 npm（Node.js）。\n"
                                   "仍要写入 ~/.npmrc 代理配置吗？"}
        return {"ok": True}

    def _persist_restore(self):
        with _cfg_lock:
            cfg = load_config()
            cfg["restore"] = self._restore
            save_config(cfg)

    def _detected_url(self, target):
        if target == "ps":
            v = pm.get_ps_status()
        elif target == "sys":
            v = pm.get_sys_proxy()
        elif target == "gitwin":
            v = pm.get_git_proxy_win()
        elif target == "gitwsl":
            v = (self.refresh_item("wsl") or {}).get("git")
        elif target == "npm":
            v = pm.get_npm_proxy()
        elif target == "pip":
            v = pm.get_pip_proxy()
        elif target == "npmwsl":
            v = pm.get_npm_proxy_wsl()
        elif target == "pipwsl":
            v = pm.get_pip_proxy_wsl()
        else:
            return None
        if not v or v in ("未设置", "未检测到"):
            return None
        s = str(v).strip()
        return s if s.startswith("http://") else None

    def _stash_previous(self, target, new_url):
        """Remember a pre-existing proxy so disable can put it back."""
        if target == "wsl" or self._restore.get(target):
            return
        cur = self._detected_url(target)
        if cur and cur != new_url:
            self._restore[target] = cur
            self._persist_restore()

    def _restore_previous(self, target):
        prev = self._restore.get(target)
        if not prev or not str(prev).startswith("http://"):
            return None
        ph, _, pp = str(prev)[len("http://"):].rpartition(":")
        if not ph or not pm._is_valid_port(pp):
            return None
        r = self.do_toggle(target, True, ph, pp, _restoring=True)
        if isinstance(r, dict) and r.get("ok"):
            self._restore.pop(target, None)
            self._persist_restore()
            extra = "已恢复覆盖前的代理"
            r["note"] = (r["note"] + "；" + extra) if r.get("note") else extra
            return r
        return r if isinstance(r, dict) else {"error": "恢复覆盖前的代理失败"}

    def clear_all_endpoints(self):
        """Disable every endpoint (used by 退出时关闭代理). Restores stashed
        values when we had overwritten them."""
        for t in ("ps", "sys", "wsl", "gitwin", "gitwsl", "npm", "pip",
                  "npmwsl", "pipwsl"):
            try:
                self.do_toggle(t, False)
            except Exception:
                pass
        return True

    def do_toggle(self, target, on, host=None, port=None, _restoring=False):
        # Enabling requires a valid address; disabling must never be blocked
        # by one (ps/wsl removal ignores the address, and git falls back to
        # the managed/detected URL), so only validate on the enable path.
        addr = self._endpoint_addr(host, port)
        if on:
            if not addr:
                return {"error": "代理地址或端口不合法，请先修正"}
            h, p = addr
        else:
            h, p = addr if addr else (self.host, self.port)
        # Keep the cache in sync so later calls without explicit address
        # (speed probe, restart_wsl) act on the address just applied.
        # Restoring a previous proxy must not overwrite the UI's current fields.
        if not _restoring:
            self.host, self.port = h, p
        notes = []
        if on and not _restoring:
            self._stash_previous(target, f"http://{h}:{p}")
        if (not on) and (not _restoring):
            restored = self._restore_previous(target)
            if restored is not None:
                return restored
        if target == "ps":
            if not on:
                # Surgical removal only drops the URL it is given, so use the
                # value actually in the registry (the user may have edited the
                # fields since enabling — same contract as npm/pip below).
                cur = pm.get_ps_status()
                if cur and str(cur).startswith("http://"):
                    ch, _, cp = str(cur)[len("http://"):].rpartition(":")
                    if ch and pm._is_valid_port(cp):
                        h, p = ch, cp
            ret = pm.set_ps_proxy(h, p, on)
            if ret is not True:
                return {"error": f"PowerShell: {ret}"}
            if on:
                notes.append("请新开一个终端后生效")
        elif target == "sys":
            if not on:
                # Same rationale as ps: unset the URL actually configured,
                # not whatever the (possibly re-edited) fields hold now.
                cur = pm.get_sys_proxy()
                if cur and str(cur).startswith("http://"):
                    ch, _, cp = str(cur)[len("http://"):].rpartition(":")
                    if ch and pm._is_valid_port(cp):
                        h, p = ch, cp
            ret = pm.set_sys_proxy(h, p, on)
            if ret is not True:
                return {"error": f"系统代理: {ret}"}
        elif target == "wsl":
            if on and h.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。")
            ret = pm.set_wsl_proxy(h, p, on)
            if ret is not True:
                return {"error": f"WSL: {ret}"}
            if on:
                notes.append("已写入 ~/.bashrc、~/.zshrc 与 fish conf.d，请新开终端后生效")
        elif target in ("gitwin", "gitwsl"):
            label = "Git(Windows)" if target == "gitwin" else "Git(WSL)"
            use_wsl = target == "gitwsl"
            if on:
                # Git(WSL) writes the URL verbatim (pm has no substitution),
                # so the NAT gateway swap must happen here — same contract as
                # the wsl/npmwsl/pipwsl endpoints.
                gh, gp = self._wsl_url_target(h, p) if use_wsl else (h, p)
                if use_wsl and (gh, gp) != (h, p):
                    notes.append("NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。")
                ret = pm.set_git_proxy(gh, gp, True, use_wsl=use_wsl)
                if ret is not True:
                    return {"error": f"{label}: {ret}"}
                self._git_managed[target] = (gh, gp)
            else:
                # Unset the URL this tool actually wrote, not whatever the
                # fields hold now (the user may have edited them since).
                gh, gp = self._git_unset_target(target)
                ret = pm.set_git_proxy(gh, gp, False, use_wsl=use_wsl)
                self._git_managed[target] = None
                if ret is not True:
                    return {"error": f"{label}: {ret}"}
        elif target in ("npm", "pip"):
            if not on:
                # Surgical unset needs the URL this tool wrote, not whatever
                # the fields hold now (the user may have edited them since):
                # prefer the value actually in the config file.
                cur = pm.get_npm_proxy() if target == "npm" else pm.get_pip_proxy()
                if cur and cur.startswith("http://"):
                    ch, _, cp = cur[len("http://"):].rpartition(":")
                    if ch and pm._is_valid_port(cp):
                        h, p = ch, cp
            fn = pm.set_npm_proxy if target == "npm" else pm.set_pip_proxy
            ret = fn(h, p, on)
            if ret is not True:
                return {"error": f"{target}: {ret}"}
        elif target in ("npmwsl", "pipwsl"):
            if on and h.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。")
            if not on:
                # Same rationale as the Windows npm/pip endpoints: unset the
                # value actually present in the distro's config file.
                cur = pm.get_npm_proxy_wsl() if target == "npmwsl" else pm.get_pip_proxy_wsl()
                if cur and cur.startswith("http://"):
                    ch, _, cp = cur[len("http://"):].rpartition(":")
                    if ch and pm._is_valid_port(cp):
                        h, p = ch, cp
            fn = pm.set_npm_proxy_wsl if target == "npmwsl" else pm.set_pip_proxy_wsl
            ret = fn(h, p, on)
            if ret is not True:
                return {"error": f"{target}: {ret}"}
        else:
            return {"error": f"未知端点 {target}"}
        return {"ok": True, "note": "；".join(notes) if notes else None}

    def _wsl_url_target(self, host, port):
        """Host/port that WSL-side endpoints should write: in NAT mode a
        loopback host is unreachable from the distro, so it becomes the
        Windows gateway IP (mirrored mode keeps the loopback address)."""
        port = str(port)
        if host.lower() in pm.LOOPBACK_HOSTS \
                and pm.get_wsl_network_mode() != "Mirrored":
            gw = pm._get_wsl_gateway_ip()
            if gw:
                return gw, port
        return host, port

    def _git_unset_target(self, target):
        """Address whose git proxy a disable should remove: this session's
        managed URL if known, else whatever git currently has configured
        (survives restarts, where _git_managed starts empty)."""
        if self._git_managed.get(target):
            return self._git_managed[target]
        v = pm.get_git_proxy_win() if target == "gitwin" \
            else (self.refresh_item("wsl") or {}).get("git")
        if v and v.startswith("http://"):
            u = v[len("http://"):]
            h, _, p = u.rpartition(":")
            if h and pm._is_valid_port(p):
                return (h, p)
        return (self.host, self.port)

    # ── quick port switch: re-apply enabled endpoints on the new port ──
    def switch_port(self, new_port, host=None):
        new_port = str(new_port).strip()
        if not pm._is_valid_port(new_port):
            return {"error": "端口须为 1-65535 的整数"}
        new_port = str(int(new_port))
        new_host = str(host).strip() if host else self.host
        if not _valid_host(new_host):
            return {"error": "代理地址不合法，请先修正"}
        if new_port == self.port and new_host == self.host:
            return {"ok": True, "applied": []}

        # Match currently-applied endpoints against the CACHED address, not
        # the (possibly just-edited) field host. Re-enable uses new_host.
        old_h, old_p = self.host, str(self.port)
        if not _valid_host(old_h) or not pm._is_valid_port(old_p):
            old_h, old_p = new_host, old_p if pm._is_valid_port(old_p) else new_port
        managed_url = f"http://{old_h}:{old_p}"
        # The WSL npm/pip endpoints write the gateway URL in NAT mode, so the
        # "is this ours" comparison must use the same substituted URL.
        wsl_managed_url = managed_url
        if old_h.lower() in pm.LOOPBACK_HOSTS \
                and pm.get_wsl_network_mode() != "Mirrored":
            gw = pm._get_wsl_gateway_ip()
            if gw:
                wsl_managed_url = f"http://{gw}:{old_p}"
        # Detect what is currently on. Only values equal to this tool's
        # managed URL count as "ours" — a proxy the user configured by hand
        # must survive the switch untouched (one wsl.exe call for the WSL side).
        wsl_state = self.refresh_item("wsl")
        enabled = {
            "ps": pm.get_ps_status() == managed_url,
            "sys": pm.get_sys_proxy() == managed_url,
            "wsl": bool((wsl_state or {}).get("on")),
            "gitwin": pm.get_git_proxy_win() == managed_url,
            "gitwsl": (wsl_state or {}).get("git") == wsl_managed_url,
            "npm": pm.get_npm_proxy() == managed_url,
            "pip": pm.get_pip_proxy() == managed_url,
            "npmwsl": pm.get_npm_proxy_wsl() == wsl_managed_url,
            "pipwsl": pm.get_pip_proxy_wsl() == wsl_managed_url,
        }
        if not any(enabled.values()):
            self.host, self.port = new_host, new_port
            with _cfg_lock:
                if not save_config(self._cfg_with_port(new_port)):
                    return {"ok": False, "error": "配置文件写入失败（磁盘已满或权限不足）"}
            return {"ok": True, "applied": []}

        errs, notes = {}, []
        # Disable with the OLD port first (git's surgical unset needs the
        # exact managed URL), then re-enable each endpoint on the new one.
        dis_errs = {}
        if enabled["ps"]:
            ret = pm.set_ps_proxy(old_h, old_p, False)
            if ret is not True:
                dis_errs["ps"] = f"PowerShell: {ret}"
        if enabled["sys"]:
            ret = pm.set_sys_proxy(old_h, old_p, False)
            if ret is not True:
                dis_errs["sys"] = f"系统代理: {ret}"
        if enabled["wsl"]:
            ret = pm.set_wsl_proxy(old_h, old_p, False)
            if ret is not True:
                dis_errs["wsl"] = f"WSL: {ret}"
        # Git(WSL) in NAT mode holds the gateway URL — unset with the exact
        # URL detected above, not the loopback fields.
        gitwsl_unset = (old_h, old_p)
        if enabled["gitwsl"]:
            gv = (wsl_state or {}).get("git")
            if gv and gv.startswith("http://"):
                gh, _, gp = gv[len("http://"):].rpartition(":")
                if gh and pm._is_valid_port(gp):
                    gitwsl_unset = (gh, gp)
        for tgt, label, use_wsl in (("gitwin", "Git(Windows)", False),
                                    ("gitwsl", "Git(WSL)", True)):
            if enabled[tgt]:
                uh, up = gitwsl_unset if tgt == "gitwsl" else (old_h, old_p)
                ret = pm.set_git_proxy(uh, up, False, use_wsl=use_wsl)
                if ret is not True:
                    dis_errs[tgt] = f"{label}: {ret}"
        if enabled["npm"]:
            ret = pm.set_npm_proxy(old_h, old_p, False)
            if ret is not True:
                dis_errs["npm"] = f"npm: {ret}"
        if enabled["pip"]:
            ret = pm.set_pip_proxy(old_h, old_p, False)
            if ret is not True:
                dis_errs["pip"] = f"pip: {ret}"
        if enabled["npmwsl"]:
            # In NAT mode the file holds the gateway URL, so the surgical
            # disable must target the substituted host, not the fields'.
            wh, wp = old_h, old_p
            cur = pm.get_npm_proxy_wsl()
            if cur and cur.startswith("http://"):
                ch, _, cp = cur[len("http://"):].rpartition(":")
                if ch and pm._is_valid_port(cp):
                    wh, wp = ch, cp
            ret = pm.set_npm_proxy_wsl(wh, wp, False)
            if ret is not True:
                dis_errs["npmwsl"] = f"npm(WSL): {ret}"
        if enabled["pipwsl"]:
            wh, wp = old_h, old_p
            cur = pm.get_pip_proxy_wsl()
            if cur and cur.startswith("http://"):
                ch, _, cp = cur[len("http://"):].rpartition(":")
                if ch and pm._is_valid_port(cp):
                    wh, wp = ch, cp
            ret = pm.set_pip_proxy_wsl(wh, wp, False)
            if ret is not True:
                dis_errs["pipwsl"] = f"pip(WSL): {ret}"

        if dis_errs:
            return {"ok": False, "applied": [],
                    "error": "；".join(dis_errs.values()), "note": None}

        self.host, self.port = new_host, new_port
        cfg_err = None
        with _cfg_lock:
            if not save_config(self._cfg_with_port(new_port)):
                cfg_err = "配置文件写入失败（磁盘已满或权限不足）"

        applied = []
        if enabled["wsl"]:
            pm._invalidate_wsl_network_mode_cache()
            ret = pm.set_wsl_proxy(new_host, new_port, True)
            if ret is True:
                applied.append("wsl")
            else:
                errs["wsl"] = f"WSL: {ret}"
        if enabled["ps"]:
            ret = pm.set_ps_proxy(new_host, new_port, True)
            if ret is True:
                applied.append("ps")
            else:
                errs["ps"] = f"PowerShell: {ret}"
        if enabled["sys"]:
            ret = pm.set_sys_proxy(new_host, new_port, True)
            if ret is True:
                applied.append("sys")
            else:
                errs["sys"] = f"系统代理: {ret}"
        for tgt, label, use_wsl in (("gitwin", "Git(Windows)", False),
                                    ("gitwsl", "Git(WSL)", True)):
            if enabled[tgt]:
                # NAT: Git(WSL) must re-enable with the gateway URL
                # (set_git_proxy writes verbatim — no pm-side swap).
                gh, gp = self._wsl_url_target(new_host, new_port) \
                    if use_wsl else (new_host, new_port)
                ret = pm.set_git_proxy(gh, gp, True, use_wsl=use_wsl)
                if ret is True:
                    applied.append(tgt)
                    self._git_managed[tgt] = (gh, gp)
                else:
                    errs[tgt] = f"{label}: {ret}"
        if enabled["npm"]:
            ret = pm.set_npm_proxy(new_host, new_port, True)
            if ret is True:
                applied.append("npm")
            else:
                errs["npm"] = f"npm: {ret}"
        if enabled["pip"]:
            ret = pm.set_pip_proxy(new_host, new_port, True)
            if ret is True:
                applied.append("pip")
            else:
                errs["pip"] = f"pip: {ret}"
        if enabled["npmwsl"]:
            ret = pm.set_npm_proxy_wsl(new_host, new_port, True)
            if ret is True:
                applied.append("npmwsl")
            else:
                errs["npmwsl"] = f"npm(WSL): {ret}"
        if enabled["pipwsl"]:
            ret = pm.set_pip_proxy_wsl(new_host, new_port, True)
            if ret is True:
                applied.append("pipwsl")
            else:
                errs["pipwsl"] = f"pip(WSL): {ret}"
        if "wsl" in applied or "npmwsl" in applied or "pipwsl" in applied \
                or "gitwsl" in applied:
            if new_host.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式已自动改用宿主网关 IP。")
        if cfg_err:
            errs["config"] = cfg_err
        ok = not errs
        return {"ok": ok, "applied": applied,
                "error": "；".join(errs.values()) if errs else None,
                "note": "；".join(notes) if notes else None}

    def _cfg_with_port(self, new_port):
        cfg = load_config()
        cfg["host"], cfg["port"] = self.host, new_port
        return cfg

    # ── live speed + port probe (sampled on a background thread) ──
    def _speed_loop(self):
        last_t = time.monotonic()
        while True:
            time.sleep(1.0)
            try:
                now = time.monotonic()
                dt = max(now - last_t, 1e-3)
                last_t = now
                cur = pm.get_net_counters()
                # Snapshot host/port atomically: do_toggle writes them as a
                # pair on another thread without this lock.
                host, port = self.host, self.port
                up_str = dn_str = None
                dn_num = 0.0
                with self._speed_lock:
                    if cur is not None and self._speed_prev is not None:
                        up = (cur[0] - self._speed_prev[0]) / dt
                        dn = (cur[1] - self._speed_prev[1]) / dt
                        if 0 <= up <= 100 * 1024 * 1024:
                            up_str = pm.format_speed(up)
                        if 0 <= dn <= 100 * 1024 * 1024:
                            dn_str = pm.format_speed(dn)
                            dn_num = float(dn)
                    self._speed_prev = cur if cur is not None else self._speed_prev
                # TCP probe can block ~1.5s when the proxy is down — keep it
                # off the speed lock so get_speed() stays responsive.
                alive = None
                probed_port = ""
                if host and pm._is_valid_port(port):
                    alive = pm.proxy_port_alive(host, port)
                    probed_port = str(int(port))
                with self._speed_lock:
                    self._speed = {
                        "up": up_str or "—", "dn": dn_str or "—",
                        "dn_num": dn_num,
                        "iface": pm._primary_iface or "",
                        "alive": alive,
                        "port": probed_port,
                    }
            except Exception:
                pass  # never let the sampler thread die

    def get_speed(self):
        with self._speed_lock:
            return dict(self._speed)

    # ── misc actions ──
    def restart_wsl(self):
        pm._invalidate_wsl_network_mode_cache()
        ret = pm.restart_wsl()
        # A WSL restart can move the NAT subnet; refresh the gateway IP baked
        # into every WSL-side endpoint (bashrc, git, npm, pip) when the proxy
        # is on with a loopback host.
        if ret is True \
                and self.host.lower() in pm.LOOPBACK_HOSTS \
                and pm._is_valid_port(self.port) \
                and pm.get_wsl_network_mode() != "Mirrored":
            h, p = self.host, str(int(self.port))
            wsl_state = self.refresh_item("wsl")
            if wsl_state and wsl_state.get("on"):
                pm.set_wsl_proxy(h, p, True)
            git = (wsl_state or {}).get("git")
            if git and str(git).startswith("http://"):
                gh, gp = self._wsl_url_target(h, p)
                pm.set_git_proxy(gh, gp, True, use_wsl=True)
                self._git_managed["gitwsl"] = (gh, gp)
            npm = pm.get_npm_proxy_wsl()
            if npm and str(npm).startswith("http://"):
                pm.set_npm_proxy_wsl(h, p, True)
            pipv = pm.get_pip_proxy_wsl()
            if pipv and str(pipv).startswith("http://"):
                pm.set_pip_proxy_wsl(h, p, True)
        return {"ok": ret is True, "error": None if ret is True else str(ret)}

    def copy(self, text):
        """Clipboard fallback for pages where navigator.clipboard is
        unavailable. clip.exe takes the console codepage; every copyable value
        (IPs, proxy URLs) is ASCII, so that is safe."""
        try:
            subprocess_run = __import__("subprocess").run
            subprocess_run(["clip.exe"], input=(text + "\n").encode("utf-8"),
                           creationflags=0x08000000, check=True)  # CREATE_NO_WINDOW
            return True
        except Exception:
            return False

    def test_channel(self, which):
        """Connectivity probe for one proxy variable chip. which selects the
        proxy source:
          ps / gitwin / npm / pip  -> Windows user proxy sources
          wsl / gitwsl / npmwsl / pipwsl -> WSL-side sources
        The probe fetches a lightweight URL THROUGH the corresponding proxy
        (source read from the same config files/env the tool manages) and
        reports success + latency. Direct (no-proxy) probes are NOT done.
        """
        import time as _t
        import urllib.request as _urlreq

        sources = {
            # Windows side. The _https variants probe the HTTPS_PROXY variable
            # itself, so a hand-configured https:// proxy is tested as such
            # instead of silently reporting the HTTP row's result.
            "ps":     pm.get_ps_proxy_vars,      # env vars (HTTP_PROXY)
            "ps_https": lambda: {"HTTPS_PROXY":
                                 pm.get_ps_proxy_vars()["HTTPS_PROXY"]},
            "sys":    lambda: {"HTTP_PROXY": pm.get_sys_proxy()},
            "gitwin": lambda: {"HTTP_PROXY": pm.get_git_proxy_win()},
            "npm":    lambda: {"HTTP_PROXY": pm.get_npm_proxy()},
            "pip":    lambda: {"HTTP_PROXY": pm.get_pip_proxy()},
        }
        fn = sources.get(which)
        if fn:
            vars_ = fn()
            url = vars_.get("HTTP_PROXY") or vars_.get("HTTPS_PROXY")
            if not url or not str(url).startswith("http"):
                return {"ok": False, "error": "未设置代理，无法测试"}
            host, _, port = str(url)[len("http://"):].rpartition(":")
            if not host or not pm._is_valid_port(port) or not _valid_host(host):
                return {"ok": False, "error": f"代理地址无效：{url}"}
            return self._proxy_probe(host, int(port), _t, _urlreq)
        # WSL side: the proxy URL lives inside the distro; probe THROUGH it
        # from within WSL so the path matches what npm/pip/git will use.
        wsl_state = self.refresh_item("wsl") if which == "gitwsl" else None
        wsl_sources = {
            "wsl":    lambda: pm.get_wsl_proxy_vars(),
            "wsl_https": lambda: {"HTTPS_PROXY":
                                  pm.get_wsl_proxy_vars()["HTTPS_PROXY"]},
            "gitwsl": lambda: {"HTTP_PROXY": (wsl_state or {}).get("git", "未设置")},
            "npmwsl": lambda: {"HTTP_PROXY": pm.get_npm_proxy_wsl()},
            "pipwsl": lambda: {"HTTP_PROXY": pm.get_pip_proxy_wsl()},
        }
        wfn = wsl_sources.get(which)
        if not wfn:
            return {"ok": False, "error": f"未知渠道 {which}"}
        url = (wfn() or {}).get("HTTP_PROXY")
        if not url or not str(url).startswith("http"):
            return {"ok": False, "error": "未设置代理，无法测试"}
        host, _, port = str(url)[len("http://"):].rpartition(":")
        if not host or not pm._is_valid_port(port) or not _valid_host(host):
            return {"ok": False, "error": f"代理地址无效：{url}"}
        # Probe from inside WSL (curl) so NAT/mirrored routing matches reality.
        return self._wsl_proxy_probe(host, int(port))

    def _proxy_probe(self, host, port, _t, urlreq):
        """Fetch a lightweight URL through host:port, returning {ok, ms} or
        {ok: False, error}. Tries several endpoints so a blocked gstatic
        does not look like a dead proxy. `urlreq` is urllib.request."""
        proxy = urlreq.ProxyHandler({
            "http": f"http://{host}:{port}", "https": f"http://{host}:{port}"})
        opener = urlreq.build_opener(proxy)
        last_err = "连接失败"
        t0 = _t.perf_counter()
        for dest in _PROBE_URLS:
            try:
                req = urlreq.Request(
                    dest, headers={"User-Agent": "ProxyManager/2"})
                with opener.open(req, timeout=5) as resp:
                    ok = resp.status in (200, 204)
                ms = int((_t.perf_counter() - t0) * 1000)
                if ok:
                    return {"ok": True, "ms": ms}
                last_err = f"HTTP {resp.status}"
            except Exception as e:
                last_err = str(e)[:160] or "连接失败"
        return {"ok": False, "error": last_err}

    def _wsl_proxy_probe(self, host, port):
        """Same probe executed inside WSL via curl (through the proxy)."""
        urls = " ".join(_PROBE_URLS)
        script = (
            f"for u in {urls}; do "
            f"out=$(curl -x http://{host}:{port} -s -o /dev/null "
            f"-w '%{{http_code}} %{{time_total}}' --max-time 5 \"$u\" 2>/dev/null); "
            "code=${out%% *}; "
            "if [ \"$code\" = 204 ] || [ \"$code\" = 200 ]; then echo \"$out\"; exit 0; fi; "
            "done; echo FAIL; echo"
        )
        try:
            r = subprocess.run(
                ["wsl", "-e", "bash", "-c", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
                creationflags=subprocess.CREATE_NO_WINDOW)
            out = (r.stdout or "").strip().split()
            if len(out) >= 2 and out[0] in ("204", "200"):
                return {"ok": True, "ms": int(float(out[1]) * 1000)}
            if not pm.wsl_available():
                return {"ok": False, "error": "WSL 不可用"}
            return {"ok": False, "error": f"HTTP {out[0]}" if out else "连接失败"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:120] or "连接失败"}

    def apply_form_back(self, mode):
        """Re-paint the WinForms Form background to match the theme. The form
        shows through the 9px padded ring used for edge resizing; its color is
        only chosen at window creation, so switching to dark at runtime left
        a bright ring around the page. Same marshaling as
        _apply_window_padding (Invoke to the UI thread)."""
        try:
            form = getattr(self._window, "native", None) if self._window else None
            if form is None:
                return False
            from System import Type, Func  # noqa: E402
            from System.Drawing import Color  # noqa: E402

            def _set():
                form.BackColor = Color.FromArgb(0x1B, 0x1B, 0x21) if mode == "dark" \
                    else Color.FromArgb(0xF7, 0xF6, 0xFB)

            if form.InvokeRequired:
                form.Invoke(Func[Type](_set))
            else:
                _set()
            return True
        except Exception:
            if os.environ.get("PM_DEBUG"):
                print("[pm] form back recolor failed", flush=True)
            return False

    # ── custom background ──
    def pick_background(self):
        """Native file picker via PowerShell (process-isolated, so no COM
        apartment headaches inside the pywebview process). stdout is forced
        to UTF-8 so Chinese paths survive the console round-trip."""
        # A topmost window would cover the dialog (it is another process).
        was_pin = self._pinned
        if was_pin:
            self._apply_topmost(False)
        ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
              "$f = New-Object System.Windows.Forms.OpenFileDialog; "
              "$f.Filter = '图片|*.png;*.jpg;*.jpeg;*.webp;*.bmp'; "
              "$f.Title = '选择背景图片'; "
              "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.FileName }")
        try:
            r = __import__("subprocess").run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps],
                capture_output=True, timeout=120, encoding="utf-8",
                errors="replace", creationflags=0x08000000)
            path = (r.stdout or "").strip().splitlines()
            path = path[-1].strip() if path else ""
            if not path or not os.path.isfile(path):
                return {"ok": False}  # user cancelled
            try:
                if os.path.getsize(path) > _MAX_BG_BYTES:
                    return {"ok": False, "error": "图片超过 8MB，请选择更小的文件"}
            except OSError as e:
                return {"ok": False, "error": str(e)}
            if not self.save_config({"bg_path": path}):
                return {"ok": False, "error": "配置文件写入失败（磁盘已满或权限不足）"}
            return {"ok": True, "path": path, "name": os.path.basename(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            if was_pin:
                self._apply_topmost(True)

    def clear_background(self):
        self.save_config({"bg_path": ""})
        return True

    def get_background(self):
        """Background image as a data URL (the page is served over http://, so
        it cannot reference file:// paths directly). Cache key includes
        mtime+size so an edited image at the same path is re-read."""
        cfg = load_config()
        path = cfg.get("bg_path", "")
        if not path or not os.path.isfile(path):
            return None
        try:
            stat = os.stat(path)
            key = (path, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return None
        if stat.st_size > _MAX_BG_BYTES:
            return {"error": "图片超过 8MB，请选择更小的文件"}
        if self._bg_cache and self._bg_cache[0] == key:
            return {"data": self._bg_cache[1]}
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                "webp": "webp", "bmp": "bmp"}.get(ext)
        if not mime:
            return None
        try:
            import base64
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            data = f"data:image/{mime};base64,{b64}"
            self._bg_cache = (key, data)
            return {"data": data}
        except Exception:
            return None

    def set_autostart(self, on):
        ret = autostart_set(bool(on))
        return {"ok": ret is True, "error": None if ret is True else str(ret)}

    # ── window controls (raw Win32: see set_on_top deadlock note) ──
    def _hwnd(self):
        hwnds = _our_hwnds()
        return hwnds[0] if hwnds else None

    def window_control(self, cmd, arg=None):
        w = self._window
        if w is None:
            return {"ok": False}
        if cmd == "minimize":
            w.minimize()
        elif cmd == "maximize":
            self._toggle_maximize()
        elif cmd == "drag_move":
            # JS starts the NATIVE move loop on mousedown (like a real
            # caption): drag works with zero delay, and a fast second click
            # becomes WM_NCLBUTTONDBLCLK (handled in _resize_hook_proc) —
            # both behaviors come from the system, not from JS timers.
            # lParam MUST carry the real cursor position (0,0 would make the
            # move loop compute offsets from the top-left corner and the
            # window would jump). Must run on the UI thread.
            try:
                form = getattr(w, "native", None)
                if form is None:
                    return {"ok": False}
                from System import Type, Func  # noqa: E402

                def _move():
                    if _resize_maximized:
                        return  # dragging a maximized window is a no-op
                    hwnd = self._hwnd()
                    if not hwnd:
                        return
                    pt = _POINT()
                    if not user32.GetCursorPos(ctypes.byref(pt)):
                        return
                    # ReleaseCapture so the move loop owns the mouse.
                    user32.ReleaseCapture()
                    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
                    if lp & 0x80000000:
                        lp -= 0x100000000  # sign-extend (negative coords)
                    # WM_NCLBUTTONDOWN with HTCAPTION enters the system move
                    # loop; it returns when the button is released.
                    user32.SendMessageW(hwnd, 0x00A1, 2, lp)

                if form.InvokeRequired:
                    form.Invoke(Func[Type](_move))
                else:
                    _move()
            except Exception:
                pass
            return {"ok": True}
        elif cmd == "close":
            if self._tray_ok:
                self._save_geometry()
                w.hide()  # hide to tray, mirroring the tkinter app
            else:
                # No tray: hiding would orphan the process. Honor 退出时关闭
                # 代理 here too — the tray 退出 path must not be the only one.
                try:
                    self._save_geometry()
                    if load_config().get("quit_clear"):
                        self.clear_all_endpoints()
                except Exception:
                    pass
                w.destroy()
        elif cmd == "pin":
            self._pinned = bool(arg)
            self._apply_topmost(self._pinned)
        return {"ok": True, "maximized": self._maximized, "pinned": self._pinned}

    def _apply_topmost(self, on):
        hwnd = self._hwnd()
        if not hwnd:
            if os.environ.get("PM_DEBUG"):
                print("[pm] topmost: no hwnd", flush=True)
            return False
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        ret = user32.SetWindowPos(
            hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
            0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        if os.environ.get("PM_DEBUG"):
            err = ctypes.windll.kernel32.GetLastError()
            style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            print(f"[pm] SetWindowPos topmost={on} hwnd={hwnd:#x} ret={ret} "
                  f"err={err} exstyle={style & 0xffffffff:#x}", flush=True)
        return bool(ret)

    def _toggle_maximize(self):
        """Frameless forms cover the taskbar when natively maximized, so
        maximize = resize to the monitor work area, restore = prev bounds.

        NEVER call evaluate_js from here: window_control reaches this on a
        js_api worker thread, and evaluate_js from a worker deadlocks the UI
        thread (same class of hang as set_on_top — see module docstring).
        The taskbar path (WndProc, UI thread) pushes the icon sync itself."""
        hwnd = self._hwnd()
        if not hwnd:
            return
        if not self._maximized:
            rect = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            self._prev_bounds = (rect.left, rect.top, rect.right, rect.bottom)
            wa = _RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
            user32.SetWindowPos(hwnd, 0, wa.left, wa.top,
                                wa.right - wa.left, wa.bottom - wa.top, 0x0004)  # NOZORDER
            self._maximized = True
        else:
            if self._prev_bounds:
                left, top, right, bottom = self._prev_bounds
                user32.SetWindowPos(hwnd, 0, left, top,
                                    right - left, bottom - top, 0x0004)
            self._maximized = False
        set_resize_maximized(self._maximized)
        self._save_geometry()

    def _save_geometry(self):
        """Persist window bounds so the next launch reopens in place. While
        fake-maximized, the PRE-maximize bounds are saved alongside win_max
        so a fresh start maximizes directly and a restore lands correctly."""
        try:
            hwnd = self._hwnd()
            if not hwnd:
                return
            r = _RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return
            self._win_max = bool(self._maximized)
            if self._win_max and self._prev_bounds:
                rect = [int(v) for v in self._prev_bounds]
            else:
                rect = [r.left, r.top, r.right, r.bottom]
            self._win_rect = rect
            save_config({"win_rect": rect, "win_max": self._win_max})
        except Exception:
            pass

    def _clear_maximized_state(self):
        """Called from the WndProc hook (via _clear_fake_maximized) when a
        native caption drag restored the window behind our back: the bounds
        are already correct, only the instance flag must follow."""
        self._maximized = False


def _tray_executable_matches(executable):
    """Return whether a NotifyIcon entry belongs to this runtime family.

    Store Python records the real binary (for example pythonw3.13.exe), while
    sys.executable may be the WindowsApps alias (pythonw.exe).  Matching the
    windowless/console family keeps an unrelated python.exe tray entry from
    making the promotion retry return before our pythonw entry exists.
    """
    candidate = str(executable or "").replace("/", "\\").rsplit("\\", 1)[-1].lower()
    current = os.path.basename(sys.executable).lower()
    if getattr(sys, "frozen", False):
        return candidate == current
    if current.startswith("pythonw"):
        return candidate.startswith("pythonw") and candidate.endswith(".exe")
    if current.startswith("python"):
        return (candidate.startswith("python")
                and not candidate.startswith("pythonw")
                and candidate.endswith(".exe"))
    return candidate == current


def _promote_tray_icon(retries=6, icon=None):
    """Win11 hides newly registered tray icons in the overflow flyout, which
    makes the app look like it has no tray at all. Flip Explorer's
    IsPromoted flag for our entries so the icon shows on the taskbar.
    The entry appears in the registry only after the first NIM_ADD, hence
    the retries. Entries are matched by tooltip text (exact) — matching by
    executable path is unreliable: pythonw vs pythonw3.13 vs packaged names
    all differ between windowless/console/frozen launches. All matching
    entries (windowless + console launches each register their own) are
    promoted.

    Setting IsPromoted on a LIVE icon does not move it retroactively — the
    icon stays in the overflow until it re-registers. So after the flags are
    written, the icon is hidden and shown once (NIM_DELETE + NIM_ADD) to make
    Explorer place it on the taskbar immediately."""
    import winreg

    base = r"Control Panel\NotifyIconSettings"

    def find_targets():
        matches = []
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base, 0, winreg.KEY_READ)
        except OSError:
            return matches
        try:
            for i in range(128):
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                try:
                    sk = winreg.OpenKey(key, sub, 0, winreg.KEY_READ)
                    try:
                        tip, _ = winreg.QueryValueEx(sk, "Tooltip")
                    except FileNotFoundError:
                        tip = ""
                    try:
                        exe, _ = winreg.QueryValueEx(sk, "ExecutablePath")
                    except FileNotFoundError:
                        exe = ""
                    finally:
                        winreg.CloseKey(sk)
                except OSError:
                    continue
                # Win11 usually leaves Tooltip empty. Match the current
                # interpreter family without accepting every Python tray app;
                # accepting python.exe while we run under pythonw.exe caused
                # this retry to finish before our own entry was registered.
                if ((tip and pm.APP_TITLE in str(tip))
                        or _tray_executable_matches(exe)):
                    matches.append(sub)
        finally:
            winreg.CloseKey(key)
        return matches

    for _ in range(retries):
        targets = find_targets()
        if targets:
            promoted = 0
            for sub in targets:
                key = None
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                         base + "\\" + sub, 0,
                                         winreg.KEY_READ | winreg.KEY_SET_VALUE)
                    try:
                        cur = winreg.QueryValueEx(key, "IsPromoted")[0]
                    except FileNotFoundError:
                        cur = 0
                    if cur != 1:
                        winreg.SetValueEx(key, "IsPromoted", 0,
                                          winreg.REG_DWORD, 1)
                    promoted += 1
                except OSError:
                    pass
                finally:
                    if key is not None:
                        winreg.CloseKey(key)
            if promoted:
                # Re-register the live icon so Explorer applies the new
                # promotion state right now instead of on next launch.
                if icon is not None:
                    try:
                        icon.visible = False
                        time.sleep(0.05)
                        icon.visible = True
                    except Exception:
                        pass
                return True
        if _ + 1 < retries:
            time.sleep(3)
    return False


def _promote_tray_icon_async(icon=None):
    threading.Thread(target=_promote_tray_icon, kwargs={"icon": icon},
                     daemon=True).start()


_geometry_restored = False   # bounds are reapplied only on the first loaded


def _restore_window_geometry(api):
    """Reapply the previous session's window bounds (or fake-maximize) once,
    after the native handle exists. Physical px via SetWindowPos — passing
    them as create_window's logical width/height would double-scale on
    high-DPI displays."""
    global _geometry_restored
    if _geometry_restored or api._window is None:
        return
    _geometry_restored = True
    hwnd = api._hwnd()
    if not hwnd:
        return
    try:
        if api._win_max:
            api._toggle_maximize()  # sizes to the work area; prev bounds kept
            _sync_maximized_icon()
            return
        r = api._win_rect
        if r and all(isinstance(v, int) for v in r) \
                and (r[2] - r[0]) >= 200 and (r[3] - r[1]) >= 150:
            user32.SetWindowPos(hwnd, 0, r[0], r[1],
                                r[2] - r[0], r[3] - r[1], 0x0004)
    except Exception:
        pass


def _show_main_window(api):
    """Show, restore from minimized, and raise to the foreground. show() alone
    unhides the window BEHIND whatever is active, which reads as 'the tray
    icon does nothing'. SetForegroundWindow is permitted here because the
    tray click was delivered to this process."""
    w = api._window
    if w is None:
        return
    w.show()
    hwnd = api._hwnd()
    if not hwnd:
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def _make_tray(api, window):
    import pystray
    from PIL import Image

    def show(icon=None, item=None):
        _show_main_window(api)

    def quit_app(icon=None, item=None):
        # Mark this as a real exit before Close() fires the shared closing
        # handler. Otherwise _on_closing sees a healthy tray, hides the window
        # and cancels destruction, leaving an invisible process holding the
        # singleton mutex after the tray has already stopped.
        api._tray_ok = False
        try:
            api._save_geometry()
        except Exception:
            pass
        try:
            if load_config().get("quit_clear"):
                # clear_all_endpoints serializes 9 endpoints (WSL ones take
                # seconds each) — a balloon is the only feedback the user
                # gets while the tray menu is unresponsive.
                try:
                    if icon is not None:
                        icon.notify("正在关闭本工具写入的代理，请稍候…",
                                    pm.APP_TITLE)
                except Exception:
                    pass
                api.clear_all_endpoints()
        except Exception:
            pass
        try:
            icon.stop()
        finally:
            window.destroy()

    menu = pystray.Menu(
        pystray.MenuItem("显示主窗口", show, default=True),
        pystray.MenuItem("退出", quit_app),
    )
    icon = pystray.Icon("proxy_manager", Image.open(
        os.path.join(_BASE, "assets", "tray.png")).convert("RGBA"),
        "代理管理器", menu)
    ready = threading.Event()

    def setup(active_icon):
        active_icon.visible = True
        ready.set()

    api._icon = icon  # exposed for wake recovery and PM_DEBUG diagnostics
    threading.Thread(target=icon.run, kwargs={"setup": setup},
                     daemon=True).start()
    if not ready.wait(5):
        try:
            icon.stop()
        except Exception:
            pass
        raise RuntimeError("tray icon did not register within 5 seconds")
    return icon


def _refresh_tray_icon(api):
    """Re-register an existing instance's icon after a second launch.

    This repairs the otherwise unrecoverable state where the window is hidden,
    the process still owns the singleton mutex, and Explorer no longer shows
    its notification icon.
    """
    icon = getattr(api, "_icon", None)
    if icon is None:
        return False
    try:
        icon.visible = False
        time.sleep(0.05)
        icon.visible = True
        api._tray_ok = True
        _promote_tray_icon_async(icon)
        return True
    except Exception:
        api._tray_ok = False
        pm._log_crash(*sys.exc_info())
        return False


def _wake_or_exit():
    """Single-instance guard: a second launch wakes the first (signal file +
    Win32 activation of its window) and exits. Shares the tkinter app's mutex
    so the two front-ends never run at once. PM_NO_SINGLETON=1 bypasses the
    guard so E2E probes can run next to a live user instance."""
    if os.environ.get("PM_NO_SINGLETON"):
        return 0  # sentinel: bypassed guard (falsy for CloseHandle, non-None)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    mutex = kernel32.CreateMutexW(None, True,
                                  "Local\\ProxyManagerSingleInstance")
    last_err = ctypes.get_last_error()
    if mutex is None and last_err != 183:
        print(f"[pm] mutex create failed (err={last_err}); "
              "continuing without single-instance guard", flush=True)
    elif last_err == 183:  # ERROR_ALREADY_EXISTS
        try:
            os.makedirs(os.path.dirname(pm._SIGNAL_FILE), exist_ok=True)
            with open(pm._SIGNAL_FILE, "w") as f:
                f.write("show")
        except OSError:
            pass
        # Activate an old-code tkinter instance (exact title) or ours (prefix).
        hwnd = user32.FindWindowW(None, pm.APP_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, _SW_RESTORE)
            user32.AllowSetForegroundWindow(0xFFFFFFFF)
            user32.SetForegroundWindow(hwnd)
        else:
            for h in _our_hwnds(other_pid=True):
                user32.ShowWindow(h, _SW_RESTORE)
                user32.SetForegroundWindow(h)
        if mutex:
            kernel32.CloseHandle(mutex)
        return None
    try:
        os.remove(pm._SIGNAL_FILE)
    except OSError:
        pass
    return mutex


def main():
    sys.excepthook = pm._log_crash
    threading.excepthook = lambda a: pm._log_crash(
        a.exc_type, a.exc_value, a.exc_traceback)

    mutex = _wake_or_exit()
    if mutex is None:
        return

    cfg = load_config()
    api = Api()
    bg = "#1B1B21" if cfg.get("mode") == "dark" else "#F7F6FB"

    # Promote the tray BEFORE it registers: on every launch but the very
    # first, Explorer's registry entry already exists, so flipping
    # IsPromoted here makes the icon land VISIBLE on the taskbar at
    # NIM_ADD time. (Setting the flag after registration has no live
    # effect — the icon would stay hidden in the overflow for the whole
    # session, which users read as "no tray icon".) The post-tray
    # promotion below covers the true first launch, where the entry is
    # only created by that first NIM_ADD.
    # Only check once before registration. A first-ever launch has no entry
    # yet, so waiting here only delays both the window and tray. The retrying
    # pass runs after _make_tray has confirmed NIM_ADD completed.
    _promote_tray_icon(retries=1)

    # Cap the initial window to the primary work area at the system DPI: a
    # fixed 1060x700 logical window overflows a 1366x768 laptop at 150%.
    dpi = user32.GetDpiForSystem() or 96
    wa = _RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
    scale = dpi / 96
    win_w = min(1060, max(640, int((wa.right - wa.left) / scale)))
    win_h = min(700, max(480, int((wa.bottom - wa.top) / scale)))

    window = webview.create_window(
        "代理管理器", UI_PATH,
        js_api=api, width=win_w, height=win_h, min_size=(920, 620),
        frameless=FRAMELESS, easy_drag=False, resizable=True,
        background_color=bg, on_top=cfg.get("pin", False),
        hidden=_START_HIDDEN,  # tray autostart: no login-time window flash
    )
    api._window = window
    # Taskbar SC_RESTORE on a fake-maximized window needs the Api's
    # prev-bounds restore; the WndProc hook lives outside the Api class.
    global _restore_callback, _clear_maximized_cb, _page_window_ref
    _restore_callback = api._toggle_maximize
    _clear_maximized_cb = api._clear_maximized_state
    _page_window_ref = window

    # Tray icon lives for the whole process (like the tkinter build): the
    # frameless window has no native close button, and the X button hides to
    # tray, so the icon must exist before any hide — not only after a
    # FormClosed event, which hide() never raises anyway.
    try:
        _make_tray(api, window)
        api._tray_ok = True
        _promote_tray_icon_async(getattr(api, "_icon", None))  # Win11 hides new tray icons by default
    except Exception:
        # Windowless (pythonw) launches have no stdout — a bare print here
        # would vanish; failures must land in crash.log to be diagnosable.
        pm._log_crash(*sys.exc_info())
        print("[pm] tray unavailable", flush=True)

    # Native frameless resize: install the WM_NCHITTEST hook as soon as the
    # window exists, then keep re-installing via the watchdog below — WinForms
    # can recreate its handle at any time, which silently drops a subclass.
    # The post-tray promotion covers the true first launch (registry entry is
    # only created by that first NIM_ADD) and forces one icon re-registration
    # so Explorer applies the promoted state immediately.
    def _on_loaded():
        ensure_resize_hook(window)
        ensure_nonclient_drag(window)
        _restore_window_geometry(api)
        if _START_HIDDEN:
            try:
                window.hide()
            except Exception:
                pass
    window.events.loaded += _on_loaded

    # Alt+F4 on the frameless window should behave like the X button: hide to
    # tray (returning False cancels the native close). Tray 退出 still uses
    # destroy(), which bypasses this event entirely.
    def _on_closing():
        if api._tray_ok:
            api._save_geometry()
            window.hide()
            return False
        return True
    try:
        window.events.closing += _on_closing
    except Exception:
        pass

    def _resize_watchdog():
        while True:
            try:
                ensure_resize_hook(window)
                ensure_nonclient_drag(window)
            except Exception:
                import traceback
                traceback.print_exc()
            time.sleep(2.0)
    threading.Thread(target=_resize_watchdog, daemon=True).start()

    # Second launch wrote a wake signal: bring the window back.
    def _watch_wake():
        while True:
            if os.path.exists(pm._SIGNAL_FILE):
                try:
                    os.remove(pm._SIGNAL_FILE)
                except OSError:
                    pass
                try:
                    _refresh_tray_icon(api)
                    _show_main_window(api)
                except Exception:
                    pass
            time.sleep(1.5)
    threading.Thread(target=_watch_wake, daemon=True).start()

    print("[pm] starting webview loop", flush=True)
    if os.environ.get("PM_DEBUG"):
        # Smoke-test probe: evaluate_js rides the same UI-thread marshaling as
        # api return values, so a stalled "done" line means the pump is stuck.
        def _heartbeat():
            i = 0
            while True:
                i += 1
                print(f"[pm] heartbeat {i} ...", flush=True)
                try:
                    window.evaluate_js("1+1")
                    print(f"[pm] heartbeat {i} done", flush=True)
                except Exception as e:
                    print(f"[pm] heartbeat FAILED: {e}", flush=True)
                    return
                time.sleep(2)
        threading.Thread(target=_heartbeat, daemon=True).start()

        # Self-test probe: exercise maximize/restore/pin and report the real
        # window geometry / WS_EX_TOPMOST style so the smoke test can assert.
        def _window_probe():
            time.sleep(6)
            hwnd = api._hwnd()
            if not hwnd:
                print("[probe] no hwnd found", flush=True)
                return

            def rect():
                r = _RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                return (r.left, r.top, r.right, r.bottom)

            wa = _RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
            work = (wa.left, wa.top, wa.right, wa.bottom)
            print(f"[probe] initial {rect()} workarea {work}", flush=True)

            api.window_control("maximize")
            time.sleep(1)
            r = rect()
            ok_max = r == work
            print(f"[probe] maximized {r} MAX_OK={ok_max}", flush=True)

            api.window_control("maximize")
            time.sleep(1)
            print(f"[probe] restored {rect()}", flush=True)

            def topmost():
                return bool(user32.GetWindowLongW(hwnd, -20) & 0x8)  # GWL_EXSTYLE, WS_EX_TOPMOST

            api.window_control("pin", True)
            time.sleep(0.5)
            print(f"[probe] pin on topmost={topmost()}", flush=True)
            api.window_control("pin", False)
            time.sleep(0.5)
            print(f"[probe] pin off topmost={topmost()}", flush=True)

            # Synthetic WM_NCHITTEST probes against the FORM: the padded ring
            # around the webview is form-owned, so real mouse input there is
            # hit-tested by the form and our hook answers with native codes.
            def hit(x, y):
                lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
                return user32.SendMessageW(hwnd, WM_NCHITTEST, 0, lp)

            time.sleep(0.5)
            rc = _RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            r = rect()
            # The WebView2 child must sit inset by the padding ring: real
            # mouse input in that ring belongs to the form (and our hook).
            kid = _RECT()
            first_child = 0

            def kcb(child, _lparam):
                nonlocal first_child
                if not first_child:
                    first_child = child
                return 1
            user32.EnumChildWindows(hwnd, _ENUMCHILD(kcb), 0)
            inset = -1
            if first_child:
                user32.GetWindowRect(first_child, ctypes.byref(kid))
                inset = kid.left - r[0]
            cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
            results = {
                "left": hit(r[0] + 2, cy), "right": hit(r[2] - 2, cy),
                "top": hit(cx, r[1] + 2), "bottom": hit(cx, r[3] - 2),
                "corner_br": hit(r[2] - 2, r[3] - 2),
                "center": hit(cx, cy),
            }
            expect = {"left": 10, "right": 11, "top": 12, "bottom": 15,
                      "corner_br": 17, "center": 1}
            print(f"[probe] child_inset={inset}px nchittest {results} EXPECT {expect} "
                  f"RESIZE_OK={all(results[k] == v for k, v in expect.items())}",
                  flush=True)
        threading.Thread(target=_window_probe, daemon=True).start()
    try:
        webview.start(debug=bool(os.environ.get("PM_DEBUG")))
        print("[pm] webview.start returned normally", flush=True)
    except Exception:
        import traceback
        traceback.print_exc()
        raise

    ctypes.windll.kernel32.CloseHandle(mutex) if mutex else None


if __name__ == "__main__":
    main()
