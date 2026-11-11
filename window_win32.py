"""Win32 frameless-window helpers: resize hook, padding, caption drag."""

import ctypes
import os
import threading

import proxy_manager as pm

user32 = ctypes.WinDLL("user32")


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", ctypes.c_uint)]


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
user32.MonitorFromWindow.argtypes = [ctypes.c_ssize_t, ctypes.c_uint]
user32.MonitorFromWindow.restype = ctypes.c_ssize_t
user32.MonitorFromRect.argtypes = [ctypes.POINTER(_RECT), ctypes.c_uint]
user32.MonitorFromRect.restype = ctypes.c_ssize_t
user32.GetMonitorInfoW.argtypes = [ctypes.c_ssize_t, ctypes.POINTER(_MONITORINFO)]
user32.GetMonitorInfoW.restype = ctypes.c_int
_SW_RESTORE = 9
_MONITOR_DEFAULTTONEAREST = 2
_MONITOR_DEFAULTTONULL = 0


def _primary_work_area():
    wa = _RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
    return wa


def _work_area_for_hwnd(hwnd):
    """Work area of the monitor that contains `hwnd` (primary as fallback).

    SPI_GETWORKAREA is the primary monitor only — fake-maximize used it
    unconditionally, so a window on a second display jumped to monitor 1.
    """
    try:
        if hwnd:
            hmon = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
            if hmon:
                mi = _MONITORINFO()
                mi.cbSize = ctypes.sizeof(_MONITORINFO)
                if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                    return mi.rcWork
    except Exception:
        pass
    return _primary_work_area()


def _clamp_rect_visible(left, top, right, bottom):
    """If the rect intersects no monitor, move it onto the primary work area.

    Unplugging a docked display left win_rect on dead coordinates — the next
    launch restored the window off-screen with no way to reach it.
    """
    w, h = int(right) - int(left), int(bottom) - int(top)
    if w < 200:
        w = 200
    if h < 150:
        h = 150
    rc = _RECT(int(left), int(top), int(left) + w, int(top) + h)
    try:
        if user32.MonitorFromRect(ctypes.byref(rc), _MONITOR_DEFAULTTONULL):
            return [rc.left, rc.top, rc.right, rc.bottom]
    except Exception:
        pass
    wa = _primary_work_area()
    nw = min(w, max(200, wa.right - wa.left))
    nh = min(h, max(150, wa.bottom - wa.top))
    return [wa.left, wa.top, wa.left + nw, wa.top + nh]


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
WM_QUERYENDSESSION = 0x0011
_HT_EDGE = 8  # physical px margin, scaled by DPI at hit-test time

# Every trampoline ever installed must stay referenced for the whole process
# lifetime. ctypes frees a WINFUNCTYPE thunk as soon as the last reference to
# it dies, and the OLD window's WndProc slot still points at that memory —
# WinForms recreates handles, so a second install used to drop the only strong
# reference and the previous window crashed on its next message (use-after-free
# that only showed up intermittently). Append here, never reassign.
_resize_wndproc_refs = []    # trampoline keepalives
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
_quit_clear_cb = None      # set by main(): session-end (shutdown/logoff) clear
_session_clear_started = False


def _sync_maximized_icon():
    """Push the maximize/restore icon state to the page from a WndProc
    context. evaluate_js must NOT run on the UI thread (the JS-result
    callback needs the message pump we are currently inside — it deadlocks);
    a short-lived worker thread is the safe caller (pywebview Invoke-marshals
    the script to the UI pump)."""
    w = _page_window_ref
    if w is None:
        return
    with _resize_hook_lock:
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
    with _resize_hook_lock:
        _resize_maximized = False
    if _clear_maximized_cb is not None:
        try:
            _clear_maximized_cb()
        except Exception:
            pass
    _sync_maximized_icon()


def _run_session_clear():
    """WM_QUERYENDSESSION side effect: run the quit-clear once, off the UI
    thread, and never wait for it (see the handler). One-shot so a second
    query (or a second window) cannot start a parallel clear."""
    global _session_clear_started
    with _resize_hook_lock:
        if _session_clear_started:
            return
        _session_clear_started = True
        cb = _quit_clear_cb

    def _work():
        try:
            if cb is not None:
                cb()
        except Exception:
            pm.log_exception("session_end_quit_clear")

    threading.Thread(target=_work, daemon=True).start()


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
    # Snapshot the previous WndProc for THIS dispatch: a concurrent re-install
    # (WinForms handle recreation) must never leave an in-flight message
    # calling a pointer that was just cleared.
    old_proc = _resize_old_proc
    if msg == WM_NCHITTEST:
        try:
            with _resize_hook_lock:
                maximized = _resize_maximized
            if not maximized and _resize_form_hwnd:
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
        except Exception:
            pass
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
            if not _is_maximized() and _restore_callback is not None:
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
            if _is_maximized() and _restore_callback is not None:
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
        if wparam == 0 and _is_maximized():  # SIZE_RESTORED
            r = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            wa = _work_area_for_hwnd(hwnd)
            if (r.left, r.top, r.right, r.bottom) != \
                    (wa.left, wa.top, wa.right, wa.bottom):
                _clear_fake_maximized()
    elif msg == 0x0232:  # WM_EXITSIZEMOVE
        # Dragging the caption of a fake-maximized window: the system move
        # loop just MOVES it (it doesn't know the window is "maximized"), so
        # no WM_SIZE/SC_RESTORE fires and the state would go stale. When the
        # loop ends and the bounds no longer match the work area, the user
        # really dragged it off — route it through the SAME restore as
        # SC_RESTORE so the window returns to its pre-maximize bounds.
        # Only clearing the flag (what this branch used to do) left the window
        # sitting at work-area size: the next maximize captured that size as
        # _prev_bounds and the original bounds were lost for good.
        # (A plain caption click also enters/exits the loop, but bounds still
        # match the work area, so nothing happens.)
        if _is_maximized() and _restore_callback is not None:
            r = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            wa = _work_area_for_hwnd(hwnd)
            if (r.left, r.top, r.right, r.bottom) != \
                    (wa.left, wa.top, wa.right, wa.bottom):
                try:
                    # _toggle_maximize() repositions the window itself; it
                    # cannot push the icon sync (it also runs on js_api
                    # worker threads), which is why SC_RESTORE syncs here.
                    _restore_callback()
                except Exception:
                    pass  # the flag is still cleared below
                # Reconcile all three views (module flag, Api flag, JS icon)
                # unconditionally: if the prev bounds happen to equal the work
                # area the restore is a no-op, no WM_SIZE arrives to clear the
                # flag, and the icon would claim "maximized" over a window
                # that is not. Also the fallback when _restore_callback raised.
                _clear_fake_maximized()
    elif msg == WM_QUERYENDSESSION:
        # Shutdown / logoff. 退出时关闭代理 must run here too, but the UI
        # thread is inside the session-end query and clear_all_endpoints
        # touches the registry and wsl.exe subprocesses (seconds per WSL
        # endpoint) — calling it inline, or letting it marshal back to this
        # very message pump via form.Invoke, deadlocks the shutdown. Run it on
        # a daemon thread and answer TRUE immediately: Windows tears the
        # process down after WM_ENDSESSION anyway, so a partial clear is
        # strictly better than a hung session end. A Python thread cannot be
        # preempted, hence no hard timeout — the bound that matters is "the UI
        # thread never waits", which this guarantees.
        _run_session_clear()
        return 1
    elif msg == WM_GETMINMAXINFO:
        # Enforce the minimum window size for the native resize loop.
        try:
            mmi = _MINMAXINFO.from_address(lparam)
            dpi = user32.GetDpiForWindow(hwnd) or 96
            # Never demand a minimum larger than the work area: a 920x620
            # logical minimum overflows a 1366x768 laptop at 150% DPI.
            wa = _work_area_for_hwnd(hwnd)
            max_w = max(320, wa.right - wa.left)
            max_h = max(240, wa.bottom - wa.top)
            mmi.ptMinTrackSize.x = min(_MIN_W * dpi // 96, max_w)
            mmi.ptMinTrackSize.y = min(_MIN_H * dpi // 96, max_h)
            return 0
        except Exception:
            pass
    return user32.CallWindowProcW(old_proc, hwnd, msg, wparam, lparam)


def _uninstall_resize_hook_locked():
    """Put the previous WndProc back on the hwnd the hook currently lives on.
    Caller must hold _resize_hook_lock.

    Installing on a NEW hwnd without this left the old window subclassed by a
    thunk whose only strong reference had just been replaced. The ownership
    check first: a recycled hwnd that now belongs to someone else's window
    must never be touched."""
    global _resize_old_proc, _resize_installed_hwnd
    old_hwnd = _resize_installed_hwnd
    old_proc = _resize_old_proc
    if not old_hwnd or not old_proc:
        _resize_old_proc = None
        _resize_installed_hwnd = 0
        return
    if _hook_is_ours(old_hwnd):
        try:
            # Restore BEFORE clearing the globals: once the slot points at the
            # original proc again no further message can reach our thunk, so
            # the reset below cannot race a dispatch in flight.
            user32.SetWindowLongPtrW(old_hwnd, _GWL_WNDPROC, old_proc)
        except Exception:
            pass  # handle already destroyed: nothing left to restore
    _resize_old_proc = None
    _resize_installed_hwnd = 0


def install_resize_hook(hwnd):
    """Subclass the form's WndProc. Re-entry guarded (loaded fires on its own
    thread and the ctypes calls release the GIL). The guard also checks WHICH
    hwnd the hook lives on: WinForms can recreate the handle, and a stale hook
    on the old hwnd must be re-installed on the new one (the old window is
    unsubclassed first — see _uninstall_resize_hook_locked)."""
    global _resize_old_proc, _resize_form_hwnd, _resize_installed_hwnd
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
            # 0 is "call failed / invalid hwnd", not "window has no styles".
            # OR-ing MINIMIZEBOX onto 0 would wipe WS_VISIBLE/WS_POPUP.
            if style and not (style & WS_MINIMIZEBOX):
                user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style | WS_MINIMIZEBOX)
                # SWP_NOSIZE|SWP_NOMOVE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_FRAMECHANGED
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
            _uninstall_resize_hook_locked()
            _resize_old_proc = user32.GetWindowLongPtrW(hwnd, _GWL_WNDPROC)
            trampoline = _WNDPROC(_resize_hook_proc)
            # Keepalive for the process lifetime — never reassigned.
            _resize_wndproc_refs.append(trampoline)
            if user32.SetWindowLongPtrW(
                    hwnd, _GWL_WNDPROC,
                    ctypes.cast(trampoline, ctypes.c_void_p).value):
                _resize_installed_hwnd = hwnd
            else:
                _resize_old_proc = None  # allow the watchdog to retry
        except Exception:
            _resize_old_proc = None


def _hook_is_ours(hwnd):
    """Whether hwnd's WndProc is one of OUR trampolines (any generation: a
    stale-but-still-installed hook still counts as ours, so the watchdog does
    not subclass an already-subclassed window)."""
    with _resize_hook_lock:
        refs = list(_resize_wndproc_refs)
    if not hwnd or not refs:
        return False
    cur = user32.GetWindowLongPtrW(hwnd, _GWL_WNDPROC)
    if not cur:
        return False
    return cur in {ctypes.cast(t, ctypes.c_void_p).value for t in refs}


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
    if (ok and window is not None and _padded_hwnd != hwnd
            and _apply_window_padding(window, _BORDER_PX)):
        _padded_hwnd = hwnd
    return ok


def resize_hook_healthy():
    """Cheap liveness probe for the resize watchdog: no EnumWindows, no
    cross-thread Invoke. True when the hook still lives on the hwnd it was
    installed on AND that hwnd still carries the padded ring — i.e. when
    ensure_resize_hook() would be a no-op anyway. A stale/recreated handle
    fails the check, so the self-heal path still runs."""
    hwnd = _resize_installed_hwnd
    return bool(hwnd) and _hook_is_ours(hwnd) and _padded_hwnd == hwnd


def _apply_window_padding(window, px):
    """Set Form.Padding on pywebview's WinForms view, marshaled to the UI
    thread via Control.Invoke (pythonnet) — the same pattern pywebview itself
    uses for minimize/maximize. The form instance is window.native.

    Padding is in DEVICE pixels while the WndProc hit-test margin is logical
    px scaled by the window DPI (_HT_EDGE * dpi / 96), so the ring is scaled
    the same way here. Without that, a 150% display hit-tests a 12px edge
    inside a 9px ring: the browser covers part of the resize margin and the
    top strip swallows caption drags."""
    try:
        form = getattr(window, "native", None)
        if form is None:
            return False
        from System import Func, Type  # noqa: E402  (pythonnet)
        from System.Windows.Forms import Padding  # noqa: E402

        def _set():
            try:
                dpi = user32.GetDpiForWindow(form.Handle) or 96
            except Exception:
                dpi = 96  # pre-1607 Windows: no per-window DPI
            form.Padding = Padding(int(px * dpi // 96))

        if form.InvokeRequired:
            form.Invoke(Func[Type](_set))
        else:
            _set()
        return True
    except Exception as e:
        pm.log_exception("apply_window_padding")
        if os.environ.get("PM_DEBUG"):
            print(f"[pm] padding failed: {e}", flush=True)
        return False


def set_resize_maximized(v):
    global _resize_maximized
    with _resize_hook_lock:
        _resize_maximized = bool(v)


def _is_maximized():
    """Thread-safe read of _resize_maximized."""
    with _resize_hook_lock:
        return _resize_maximized


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
        from System import Func, Type  # noqa: E402  (pythonnet)

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
        pm.log_exception("ensure_nonclient_drag")
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

