"""System tray icon, Win11 promotion, and window show/restore."""

import os
import sys
import threading
import time

import proxy_manager as pm
from config_store import TRAY_ICON, load_config
from window_win32 import _clamp_rect_visible, _sync_maximized_icon, user32


def _own_executable_paths():
    """Full paths this tool's process image can be recorded under. Explorer
    stores the launched binary, which may be the WindowsApps alias while
    sys.executable names the resolved store binary (or vice versa), so both
    spellings are accepted."""
    paths = {str(sys.executable or "")}
    try:
        paths.add(str(os.path.realpath(sys.executable)))
    except OSError:
        pass
    return {p.replace("/", "\\").strip().lower() for p in paths if p}


def _tray_executable_matches(executable):
    """Return whether a NotifyIcon entry's ExecutablePath is this tool's OWN
    process image (slash/case-insensitive). Matching the interpreter FAMILY
    (any pythonw*.exe / python*.exe) rewrote IsPromoted for unrelated Python
    tray programs, so only a full-path match counts now; entries that cannot
    be pinned to this tool are left alone."""
    candidate = str(executable or "").replace("/", "\\").strip().lower()
    return bool(candidate) and candidate in _own_executable_paths()


def _promote_tray_icon(retries=6, icon=None):
    """Win11 hides newly registered tray icons in the overflow flyout, which
    makes the app look like it has no tray at all. Flip Explorer's
    IsPromoted flag for our entries so the icon shows on the taskbar.
    The entry appears in the registry only after the first NIM_ADD, hence
    the retries. Entries are matched by tooltip text (exact) or by an
    ExecutablePath equal to this tool's own process image — interpreter-
    family matching rewrote IsPromoted for unrelated Python tray programs.
    All matching entries (windowless + console launches each register their
    own) are promoted.

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
                # Win11 usually leaves Tooltip empty. Promote only entries
                # pinned to THIS tool: an exact tooltip match, or an
                # ExecutablePath equal to our own process image. Anything
                # else (e.g. another Python program's tray icon) must never
                # get its registry entry rewritten.
                if (str(tip or "") in (pm.APP_TITLE, "Proxy Manager")
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
_tray_failed = False         # _make_tray never registered an icon: never trust it
_tray_lang = None            # UI language the tray strings were last built for
_tray_busy = False           # an all-on/all-off worker is running: ignore re-clicks


def _lang():
    """Current UI language, defensively (a corrupt config must not break the
    tray menu build)."""
    try:
        return load_config().get("lang", "zh")
    except Exception:
        return "zh"


def _tray_labels(lang):
    """Tray strings per UI language (zh default, en opt-in)."""
    if lang == "en":
        return {"show": "Show window", "quit": "Quit",
                "tip": "Proxy Manager",
                "allOn": "Enable all", "allOff": "Disable all",
                "countFmt": "Proxy enabled on {n} endpoint(s)",
                "busyAllOn": "Enabling endpoints one by one…",
                "busyAllOff": "Turning endpoints off…"}
    return {"show": "显示主窗口", "quit": "退出", "tip": pm.APP_TITLE,
            "allOn": "全部启用", "allOff": "全部关闭",
            "countFmt": "代理已启用 {n} 个端点",
            "busyAllOn": "正在逐个启用端点…",
            "busyAllOff": "正在关闭端点…"}


# Endpoint → the config switch the UI persists for it. "sys" (WinINET) is
# deliberately absent: its state is re-read from the registry on every
# refresh and there is no persisted switch, so the tray cannot count it
# without doing the very registry read a menu rebuild must not do.
_TRAY_TOGGLE_KEY = {
    "ps": "powershell", "wsl": "wsl", "gitwin": "git_win",
    "gitwsl": "git_wsl", "npm": "npm", "pip": "pip",
    "npmwsl": "npmwsl", "pipwsl": "pipwsl",
}
# WSL-side endpoints: ownership is proven by the persisted "last URL this
# tool wrote" record, never by re-running wsl.exe from the menu.
_TRAY_WSL_TARGETS = ("wsl", "gitwsl", "npmwsl", "pipwsl")


def _tray_all_toggle_targets():
    """Every endpoint the tray's all-on/all-off walks, in a fixed order —
    the same list clear_all_endpoints serializes over."""
    return ("ps", "sys", "wsl", "gitwin", "gitwsl",
            "npm", "pip", "npmwsl", "pipwsl")


def _tray_enabled_count(api):
    """(count, total) of endpoints currently enabled AND provably ours.

    Reads the APPLIED record only — never the form fields: a typed-but-not-
    applied address is not "on" anywhere, so counting it would show a proxy
    state the system does not have. An endpoint counts when its persisted
    switch is on AND it is provably managed by this tool:
      • Windows side: api._ours_proxy_urls(t) is non-empty (needs an applied
        record; empty means nothing to claim).
      • WSL side: api._wsl_written has a record for it (exact URL match).
    Only file reads + set arithmetic happen here — no wsl.exe, no registry:
    pystray re-reads this on every menu rebuild, so a subprocess would freeze
    the tray on right-click. Endpoints whose state cannot be proven from disk
    ("sys" has no persisted switch; the WSL terminal block has no written-URL
    record) are not counted rather than guessed at.
    """
    targets = _tray_all_toggle_targets()
    if not getattr(api, "applied_host", ""):
        return 0, len(targets)
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    count = 0
    for t in targets:
        key = _TRAY_TOGGLE_KEY.get(t)
        if not key or not cfg.get(key):
            continue
        if t in _TRAY_WSL_TARGETS:
            if (getattr(api, "_wsl_written", None) or {}).get(t):
                count += 1
        else:
            try:
                ours = api._ours_proxy_urls(t)
            except Exception:
                pm.log_exception(f"tray_count_ours:{t}")
                ours = None
            if ours:
                count += 1
    return count, len(targets)


def _tray_notify(api, icon, text):
    """Balloon feedback; a failure here is logged, never raised (the tray
    must survive a dead notification)."""
    try:
        active = icon if icon is not None else getattr(api, "_icon", None)
        if active is not None:
            active.notify(text, pm.APP_TITLE)
    except Exception:
        pm.log_exception("tray_notify")


def _tray_refresh_menu(api, icon=None):
    """Re-read the callable menu labels (enabled-endpoint count) after a
    tray-driven mutation. Same cross-thread icon use as _sync_tray_lang."""
    try:
        active = icon if icon is not None else getattr(api, "_icon", None)
        if active is not None:
            active.update_menu()
    except Exception:
        pm.log_exception("tray_update_menu")


def _tray_run_all(api, on, icon=None):
    """Tray "enable all" / "disable all" entry point.

    Must NEVER run the 9-endpoint sequence on the pystray message-loop
    thread: prepare_toggle probes each WSL target (wsl.exe, 15s timeout) and
    every WSL write takes seconds, so the menu would stop responding for
    tens of seconds. Start a daemon worker and return immediately; the user
    gets a balloon now and a result balloon later (the same pattern
    quit_app uses before its own multi-second clear). A second click while a
    worker runs is ignored instead of queuing a concurrent mutation.
    """
    global _tray_busy
    if _tray_busy:
        return
    _tray_busy = True
    labels = _tray_labels(_lang())
    _tray_notify(api, icon, labels["busyAllOn"] if on else labels["busyAllOff"])
    threading.Thread(target=_tray_all_worker, args=(api, on, icon),
                     daemon=True).start()


def _tray_all_worker(api, on, icon=None):
    global _tray_busy
    try:
        if on:
            _tray_worker_on_all(api, icon)
        else:
            # clear_all_endpoints already owns the mutation lock and decides
            # per endpoint whether the current value is ours before touching
            # it, so the tray path inherits that ownership check for free.
            try:
                api.clear_all_endpoints()
            except Exception:
                pm.log_exception("tray_all_off")
            _tray_notify(api, icon, _tray_labels(_lang())["allOff"])
    finally:
        _tray_busy = False
        _tray_refresh_menu(api, icon)


def _tray_worker_on_all(api, icon=None):
    """Serial enable of every endpoint from a daemon thread.

    The address comes from the applied record when there is one (that is what
    the endpoints are expected to hold) and falls back to the form fields
    only when nothing was ever applied. prepare_toggle results asking for
    confirmation ({confirm}) or refusing ({error}) cannot open a modal dialog
    from the tray, so those endpoints are skipped and reported; the first
    do_toggle failure stops the rest instead of writing a half-enabled set.
    """
    if getattr(api, "applied_host", "") and getattr(api, "applied_port", ""):
        host, port = api.applied_host, str(api.applied_port)
        kw = {k: (getattr(api, "applied_kw", None) or {}).get(k)
              for k in ("scheme", "user", "password")}
    else:
        host, port = api.host, api.port
        kw = {}
    done, skipped, stopped = 0, [], None
    for t in _tray_all_toggle_targets():
        try:
            pre = api.prepare_toggle(t, True, host, port)
        except Exception:
            pm.log_exception(f"tray_all_on_prepare:{t}")
            skipped.append(t)
            continue
        if not isinstance(pre, dict) or not pre.get("ok"):
            # {confirm: …} (only the window may ask) or {error: …}: skip.
            skipped.append(t)
            continue
        try:
            res = api.do_toggle(t, True, host, port, **kw)
        except Exception:
            pm.log_exception(f"tray_all_on_toggle:{t}")
            stopped = t
            break
        if isinstance(res, dict) and res.get("error"):
            stopped = t
            break
        done += 1
    en = _lang() == "en"
    bits = [_tray_labels(_lang())["countFmt"].format(n=done)]
    if skipped:
        bits.append(("skipped " if en else "跳过 ")
                    + (", " if en else "、").join(skipped))
    if stopped:
        bits.append(f"{stopped} " + ("failed, stopped" if en
                                     else "启用失败，已停止后续端点"))
    _tray_notify(api, icon, ("; " if en else "；").join(bits))


def _saved_rect_ok(r):
    """Whether r is a usable saved (l, t, r, b) rect (physical px)."""
    return bool(r) and all(isinstance(v, int) for v in r) \
        and (r[2] - r[0]) >= 200 and (r[3] - r[1]) >= 150


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
            r = api._win_rect
            seeded = False
            if _saved_rect_ok(r):
                # The window is still at its fresh default size: seed the
                # maximize toggle's "prev bounds" with the SAVED rect so the
                # later restore lands there instead of on the default.
                api._prev_bounds = tuple(_clamp_rect_visible(*r))
                seeded = True
            api._toggle_maximize(keep_prev=seeded)  # sizes to the work area
            _sync_maximized_icon()
            return
        r = api._win_rect
        if _saved_rect_ok(r):
            r = _clamp_rect_visible(*r)
            user32.SetWindowPos(hwnd, 0, r[0], r[1],
                                r[2] - r[0], r[3] - r[1], 0x0004)
    except Exception:
        pm.log_exception("restore_window_geometry")


def _show_main_window(api):
    """Show, restore from minimized, and raise to the foreground. show() alone
    unhides the window BEHIND whatever is active, which reads as 'the tray
    icon does nothing'. SetForegroundWindow is permitted here because the
    tray click was delivered to this process.

    Returns False when there is no window or no hwnd to show, so a caller can
    keep a wake request pending and retry instead of dropping it."""
    w = api._window
    if w is None:
        return False
    w.show()
    hwnd = api._hwnd()
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def _make_tray(api, window):
    global _tray_lang, _tray_failed
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
        # 退出清理期间用户点窗口 X 会触发 _on_closing：无此标志时返回 True
        # 销毁窗口 → webview 循环退出 → 进程杀死清理线程，留下半清理状态。
        api._quitting = True
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
                        # 退出清理实际做的是"关闭本工具写入的代理 + 还原用户
                        # 覆盖前的代理"，文案要说清两者，避免用户误以为
                        # 原有代理也被关闭了。
                        msg = ("Closing this tool's proxies and restoring "
                               "your previous proxy settings…"
                               if lang == "en"
                               else "正在关闭本工具写入的代理并还原您原有的代理，请稍候…")
                        icon.notify(msg, pm.APP_TITLE)
                except Exception:
                    pm.log_exception("tray_notify")
                api.clear_all_endpoints()
                # 契约 B1：标记本进程已执行过退出清理。托盘 quit_app 结尾的
                # window.destroy() 仍会触发 closing，app.py 的 _on_closing
                # 无托盘分支据此跳过第二次 clear_all_endpoints —— WSL 端点
                # 串行清理各要数秒，跑两遍退出时长直接翻倍。
                api._quit_cleared = True
        except Exception:
            pm.log_exception("tray_quit_clear")
        try:
            icon.stop()
        finally:
            # 销毁前必须复位 _quitting：pywebview 的 destroy() 走 Form.Close()
            # 仍会触发 closing 事件，_on_closing 对 _quitting 一律 return False
            # 会取消销毁 → 进程永不退出并持有单例互斥体。此刻清理已完成、
            # _tray_ok 已为 False，复位后 _on_closing 返回 True，正常销毁。
            api._quitting = False
            window.destroy()

    lang = _lang()
    labels = _tray_labels(lang)
    # pystray wraps MenuItem text in a callable and re-reads it every time the
    # menu is (re)built, so passing a function keeps the labels following the
    # UI language. A plain string froze them at startup until the next launch.
    def show_lbl(_item=None):
        return _tray_labels(_lang())["show"]

    def quit_lbl(_item=None):
        return _tray_labels(_lang())["quit"]

    def count_lbl(_item=None):
        # Read-only summary of what is actually on (applied record + persisted
        # switches); action=None renders it as a disabled menu item.
        n, _total = _tray_enabled_count(api)
        return _tray_labels(_lang())["countFmt"].format(n=n)

    def all_on_lbl(_item=None):
        return _tray_labels(_lang())["allOn"]

    def all_off_lbl(_item=None):
        return _tray_labels(_lang())["allOff"]

    def on_all(icon=None, item=None):
        _tray_run_all(api, True, icon)

    def off_all(icon=None, item=None):
        _tray_run_all(api, False, icon)

    menu = pystray.Menu(
        pystray.MenuItem(count_lbl, None),
        pystray.MenuItem(all_on_lbl, on_all),
        pystray.MenuItem(all_off_lbl, off_all),
        pystray.MenuItem(show_lbl, show, default=True),
        pystray.MenuItem(quit_lbl, quit_app),
    )
    icon = pystray.Icon("proxy_manager", Image.open(
        TRAY_ICON).convert("RGBA"),
        labels["tip"], menu)
    ready = threading.Event()

    def setup(active_icon):
        active_icon.visible = True
        ready.set()

    _tray_lang = lang
    _tray_failed = False
    api._icon = icon  # exposed for wake recovery and PM_DEBUG diagnostics
    threading.Thread(target=icon.run, kwargs={"setup": setup},
                     daemon=True).start()
    if not ready.wait(5):
        try:
            icon.stop()
        except Exception:
            pm.log_exception("tray_icon_stop")
        # api._icon is already set (wake recovery reads it), so a later
        # _refresh_tray_icon must not conclude the tray works: pystray never
        # got past registration here.
        _tray_failed = True
        raise RuntimeError("tray icon did not register within 5 seconds")
    return icon


def _tray_is_live(icon):
    """Whether pystray's message loop is really running.

    Icon._running is set by _mark_ready() (after the loop window exists) and
    cleared by stop(); it is the only state that says the icon was actually
    registered. The icon OBJECT existing proves nothing — api._icon is
    assigned before the loop thread starts."""
    if _tray_failed:
        return False
    return bool(getattr(icon, "_running", False))


def _refresh_tray_icon(api):
    """Re-register an existing instance's icon after a second launch.

    This repairs the otherwise unrecoverable state where the window is hidden,
    the process still owns the singleton mutex, and Explorer no longer shows
    its notification icon.
    """
    icon = getattr(api, "_icon", None)
    if icon is None:
        return False
    if not _tray_is_live(icon):
        # Claiming a healthy tray for an icon that never registered made the
        # X button take the "hide to tray" branch with no tray and no window
        # — an invisible process the user could not reach or close.
        api._tray_ok = False
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


def _sync_tray_lang(api):
    """Refresh the tray tooltip and menu after a UI language change.

    pystray rebuilds the menu only from update_menu(), so the callable item
    labels alone would stay in the old language until the user happened to
    click something. Returns True when the tray is usable (refreshed or
    already current)."""
    icon = getattr(api, "_icon", None)
    if icon is None or _tray_failed or not _tray_is_live(icon):
        return False
    lang = _lang()
    global _tray_lang
    if lang == _tray_lang:
        return True
    _tray_lang = lang
    try:
        # The title setter is a no-op while the icon is hidden (the new tip is
        # then used by the next _show()).
        icon.title = _tray_labels(lang)["tip"]
        icon.update_menu()  # re-reads the label callables
    except Exception:
        pm.log_exception("sync_tray_lang")
        return False
    return True

