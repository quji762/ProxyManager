"""
ProxyManager - pywebview (WebView2) front-end entry.

UI: ui/index.html (+ styles.css / i18n.js / app.js) rendered in WebView2.
System operations live in proxy_manager.py; the JS bridge is js_api.py;
Win32 chrome is window_win32.py; tray is tray_icon.py; config is
config_store.py.

    py -3.13 app.py        (or double-click start.bat)
"""

import ctypes
import os
import sys
import threading
import time

import proxy_manager as pm
from config_store import (  # noqa: F401  (re-exported for tests)
    APP_VERSION, CONFIG_FILE, UI_PATH, load_config, save_config,
    valid_host as _valid_host,
)
from js_api import Api
from tray_icon import (
    _make_tray, _promote_tray_icon, _promote_tray_icon_async,
    _refresh_tray_icon, _restore_window_geometry, _show_main_window,
)
from window_win32 import (
    WM_NCHITTEST, _ENUMCHILD, _RECT, _SW_RESTORE, _our_hwnds,
    ensure_nonclient_drag, ensure_resize_hook, user32,
)
import window_win32 as win32

FRAMELESS = True
_START_HIDDEN = any(a in ("--tray", "--hidden") for a in sys.argv[1:])


def _wake_or_exit():
    """Single-instance guard: a second launch wakes the first (signal file +
    Win32 activation of its window) and exits. PM_NO_SINGLETON=1 bypasses
    the guard so a debug instance can run next to the normal one."""
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
            os.makedirs(os.path.dirname(pm.SIGNAL_FILE), exist_ok=True)
            with open(pm.SIGNAL_FILE, "w") as f:
                f.write("show")
        except OSError:
            pass
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
        os.remove(pm.SIGNAL_FILE)
    except OSError:
        pass
    return mutex


def main():
    sys.excepthook = pm.log_crash
    threading.excepthook = lambda a: pm.log_crash(
        a.exc_type, a.exc_value, a.exc_traceback)

    mutex = _wake_or_exit()
    if mutex is None:
        return

    import webview  # GUI stack — not imported at module level so unit tests stay light

    cfg = load_config()
    api = Api()
    bg = "#1B1B21" if cfg.get("mode") == "dark" else "#F7F6FB"

    _promote_tray_icon(retries=1)

    dpi = user32.GetDpiForSystem() or 96
    wa = _RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
    scale = dpi / 96
    win_w = min(1060, max(640, int((wa.right - wa.left) / scale)))
    win_h = min(700, max(480, int((wa.bottom - wa.top) / scale)))

    window = webview.create_window(
        pm.APP_TITLE, UI_PATH,
        js_api=api, width=win_w, height=win_h, min_size=(920, 620),
        frameless=FRAMELESS, easy_drag=False, resizable=True,
        background_color=bg, on_top=cfg.get("pin", False),
        hidden=_START_HIDDEN,
    )
    api._window = window
    win32._restore_callback = api._toggle_maximize
    win32._clear_maximized_cb = api._clear_maximized_state
    win32._page_window_ref = window

    try:
        _make_tray(api, window)
        api._tray_ok = True
        _promote_tray_icon_async(getattr(api, "_icon", None))
    except Exception:
        pm.log_crash(*sys.exc_info())
        print("[pm] tray unavailable", flush=True)

    def _on_loaded():
        ensure_resize_hook(window)
        ensure_nonclient_drag(window)
        _restore_window_geometry(api)
        if _START_HIDDEN:
            try:
                window.hide()
            except Exception:
                pm.log_exception("start_hidden_hide")

    window.events.loaded += _on_loaded

    def _on_closing():
        if api._tray_ok:
            api._save_geometry()
            window.hide()
            return False
        return True

    try:
        window.events.closing += _on_closing
    except Exception:
        pm.log_exception("bind_closing")

    def _resize_watchdog():
        while True:
            try:
                ensure_resize_hook(window)
                ensure_nonclient_drag(window)
            except Exception:
                pm.log_exception_once("resize_watchdog", "resize_watchdog")
            time.sleep(2.0)

    threading.Thread(target=_resize_watchdog, daemon=True).start()

    def _watch_wake():
        while True:
            if os.path.exists(pm.SIGNAL_FILE):
                try:
                    os.remove(pm.SIGNAL_FILE)
                except OSError:
                    pass
                try:
                    _refresh_tray_icon(api)
                    _show_main_window(api)
                except Exception:
                    pm.log_exception("wake_show")
            time.sleep(1.5)

    threading.Thread(target=_watch_wake, daemon=True).start()

    print("[pm] starting webview loop", flush=True)
    if os.environ.get("PM_DEBUG"):
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

            wa2 = _RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa2), 0)
            work = (wa2.left, wa2.top, wa2.right, wa2.bottom)
            print(f"[probe] initial {rect()} workarea {work}", flush=True)

            api.window_control("maximize")
            time.sleep(1)
            r = rect()
            print(f"[probe] maximized {r} MAX_OK={r == work}", flush=True)

            api.window_control("maximize")
            time.sleep(1)
            print(f"[probe] restored {rect()}", flush=True)

            def topmost():
                return bool(user32.GetWindowLongW(hwnd, -20) & 0x8)

            api.window_control("pin", True)
            time.sleep(0.5)
            print(f"[probe] pin on topmost={topmost()}", flush=True)
            api.window_control("pin", False)
            time.sleep(0.5)
            print(f"[probe] pin off topmost={topmost()}", flush=True)

            def hit(x, y):
                lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
                return user32.SendMessageW(hwnd, WM_NCHITTEST, 0, lp)

            time.sleep(0.5)
            r = rect()
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
        pm.log_crash(*sys.exc_info())
        raise

    ctypes.windll.kernel32.CloseHandle(mutex) if mutex else None


if __name__ == "__main__":
    main()
