"""System tray icon, Win11 promotion, and window show/restore."""

import os
import sys
import threading
import time

import proxy_manager as pm
from config_store import TRAY_ICON, load_config
from window_win32 import _sync_maximized_icon, user32


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
        pm.log_exception("restore_window_geometry")


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
            pm.log_exception("tray_save_geometry")
        try:
            if load_config().get("quit_clear"):
                # clear_all_endpoints serializes 9 endpoints (WSL ones take
                # seconds each) — a balloon is the only feedback the user
                # gets while the tray menu is unresponsive.
                try:
                    if icon is not None:
                        lang = load_config().get("lang", "zh")
                        msg = ("Closing this tool's proxies…"
                               if lang == "en"
                               else "正在关闭本工具写入的代理，请稍候…")
                        icon.notify(msg, pm.APP_TITLE)
                except Exception:
                    pm.log_exception("tray_notify")
                api.clear_all_endpoints()
        except Exception:
            pm.log_exception("tray_quit_clear")
        try:
            icon.stop()
        finally:
            window.destroy()

    lang = load_config().get("lang", "zh")
    show_lbl = "Show window" if lang == "en" else "显示主窗口"
    quit_lbl = "Quit" if lang == "en" else "退出"
    menu = pystray.Menu(
        pystray.MenuItem(show_lbl, show, default=True),
        pystray.MenuItem(quit_lbl, quit_app),
    )
    icon = pystray.Icon("proxy_manager", Image.open(
        TRAY_ICON).convert("RGBA"),
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
            pm.log_exception("tray_icon_stop")
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
        pm.log_crash(*sys.exc_info())
        return False

