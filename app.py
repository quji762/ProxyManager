"""
ProxyManager - pywebview (WebView2) front-end.

The UI is a zero-build single-page web app (ui/index.html) rendered in
WebView2; every system-touching operation is delegated to the backend
library proxy_manager.py via the js_api bridge. Run:

    py -3.13 app.py        (or double-click start.bat)

Config (config v2): host, port, powershell, wsl, git_win, git_wsl, pin,
mode, ports (quick-select presets), autostart, bg_path, bg_dim, card_alpha.
A legacy {"git": bool} key migrates into git_win/git_wsl on load.

Window operations avoid pywebview's set_on_top (it writes WinForms TopMost
from the js_api worker thread and deadlocks the UI - see memory): pin and
maximize go through raw Win32 on the real HWND instead.
"""

import json
import os
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

# Frameless gives the HypoMux-style integrated title bar. Flip to False if a
# future pywebview build regresses edge-resizing on frameless windows.
FRAMELESS = True

APP_DIR = pm._config_dir()
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
AUTOSTART_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "ProxyManager"

DEFAULT_PORTS = [7890, 7892, 7897]


# ─── Config v2 (superset + legacy migration) ─────────────────────────────────
def load_config():
    defaults = {
        "host": pm.DEFAULT_HOST, "port": pm.DEFAULT_PORT,
        "powershell": True, "wsl": True, "git_win": False, "git_wsl": False,
        "pin": False, "mode": "light", "ports": list(DEFAULT_PORTS),
        "autostart": False, "bg_path": "", "bg_dim": 0.45, "card_alpha": 0.92,
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
    for k in ("powershell", "wsl", "git_win", "git_wsl", "pin", "autostart"):
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
    return out


def save_config(cfg):
    """Write the full superset; unknown keys from future versions survive."""
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
                "pin", "mode", "ports", "autostart", "bg_path", "bg_dim",
                "card_alpha")})
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2, ensure_ascii=False)
        except OSError:
            pass


_cfg_lock = threading.RLock()  # reentrant: Api.save_config nests into save_config


def _valid_host(h):
    return bool(h) and not any(c.isspace() for c in h) \
        and all(c in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in h) \
        and "://" not in h and ":" not in h


# ─── Autostart (HKCU Run key) ────────────────────────────────────────────────
def _autostart_command():
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN, 0,
                             winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            winreg.CloseKey(key)
            return os.path.abspath(__file__) in v
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
_SW_RESTORE = 9


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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
    elif msg == WM_GETMINMAXINFO:
        # Enforce the minimum window size for the native resize loop.
        try:
            mmi = _MINMAXINFO.from_address(lparam)
            dpi = user32.GetDpiForWindow(hwnd) or 96
            mmi.ptMinTrackSize.x = _MIN_W * dpi // 96
            mmi.ptMinTrackSize.y = _MIN_H * dpi // 96
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


def _enum_windows(match):
    found = []
    my_pid = os.getpid()

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
                       "iface": pm._primary_iface or "", "alive": None}
        self._window = None
        self._pinned = cfg.get("pin", False)
        self._maximized = False
        self._prev_bounds = None
        self._bg_cache = None  # (path, data_url)
        # Last URL this tool wrote to each git endpoint, so a disable after
        # the user edited host/port still unsets the old value.
        self._git_managed = {"gitwin": None, "gitwsl": None}
        self._tray_ok = False  # close falls back to quit when no tray exists
        threading.Thread(target=self._speed_loop, daemon=True).start()

    # ── config ──
    def get_config(self):
        cfg = load_config()
        return {
            "host": self.host, "port": self.port,
            "mode": cfg.get("mode", "light"), "pin": cfg.get("pin", False),
            "ports": cfg.get("ports", DEFAULT_PORTS),
            "autostart": autostart_enabled(),
            "bg_path": cfg.get("bg_path", ""),
            "bg_dim": cfg.get("bg_dim", 0.45),
            "card_alpha": cfg.get("card_alpha", 0.92),
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
            for k in ("powershell", "wsl", "git_win", "git_wsl", "pin"):
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
                cfg["bg_path"] = str(data["bg_path"])
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
            save_config(cfg)
        return True

    # ── status refresh: one call per slice, fired in parallel by the UI ──
    def refresh_item(self, which):
        if which == "ps":
            return {"status": pm.get_ps_status(),
                    "vars": pm.get_ps_proxy_vars()}
        if which == "wsl":
            ip, block, wgit = pm.get_wsl_ip_and_block()
            return {"ip": ip, "on": bool(pm.get_wsl_status(block)),
                    "vars": pm.get_wsl_proxy_vars(block), "git": wgit}
        if which == "gitwin":
            return pm.get_git_proxy_win()
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
        if target in ("wsl", "all"):
            shell = pm.wsl_default_shell()
            if shell and shell != "bash":
                return {"confirm": "shell",
                        "message": f"WSL 默认 shell 是 {shell}，而代理写入的是 ~/.bashrc"
                                   "（只有 bash 会读取它）。\n仍要继续写入吗？"}
        if target in ("gitwsl", "all") and not pm.wsl_available():
            return {"error": "未检测到可用的 WSL 发行版"}
        if target in ("gitwin", "all"):
            import shutil
            if not shutil.which("git"):
                return {"error": "未找到 git，请确认已安装 Git"}
        return {"ok": True}

    def do_toggle(self, target, on, host=None, port=None):
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
        self.host, self.port = h, p
        notes = []
        if target == "ps":
            ret = pm.set_ps_proxy(h, p, on)
            if ret is not True:
                return {"error": f"PowerShell: {ret}"}
        elif target == "wsl":
            if on and h.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。")
            ret = pm.set_wsl_proxy(h, p, on)
            if ret is not True:
                return {"error": f"WSL: {ret}"}
        elif target in ("gitwin", "gitwsl"):
            label = "Git(Windows)" if target == "gitwin" else "Git(WSL)"
            use_wsl = target == "gitwsl"
            if on:
                ret = pm.set_git_proxy(h, p, True, use_wsl=use_wsl)
                if ret is not True:
                    return {"error": f"{label}: {ret}"}
                self._git_managed[target] = (h, p)
            else:
                # Unset the URL this tool actually wrote, not whatever the
                # fields hold now (the user may have edited them since).
                gh, gp = self._git_unset_target(target)
                ret = pm.set_git_proxy(gh, gp, False, use_wsl=use_wsl)
                self._git_managed[target] = None
                if ret is not True:
                    return {"error": f"{label}: {ret}"}
        else:
            return {"error": f"未知端点 {target}"}
        return {"ok": True, "note": "；".join(notes) if notes else None}

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
        host = str(host).strip() if host else self.host
        if not _valid_host(host):
            return {"error": "代理地址不合法，请先修正"}
        if new_port == self.port and host == self.host:
            return {"ok": True, "applied": []}

        old_h, old_p = host, self.port
        managed_url = f"http://{old_h}:{old_p}"
        # Detect what is currently on. Only values equal to this tool's
        # managed URL count as "ours" — a proxy the user configured by hand
        # must survive the switch untouched (one wsl.exe call for the WSL side).
        wsl_state = self.refresh_item("wsl")
        enabled = {
            "ps": pm.get_ps_status() == managed_url,
            "wsl": bool((wsl_state or {}).get("on")),
            "gitwin": pm.get_git_proxy_win() == managed_url,
            "gitwsl": (wsl_state or {}).get("git") == managed_url,
        }
        if not any(enabled.values()):
            self.host, self.port = old_h, new_port
            with _cfg_lock:
                save_config(self._cfg_with_port(new_port))
            return {"ok": True, "applied": []}

        errs, notes = {}, []
        # Disable with the OLD port first (git's surgical unset needs the
        # exact managed URL), then re-enable each endpoint on the new one.
        dis_errs = {}
        if enabled["ps"]:
            ret = pm.set_ps_proxy(old_h, old_p, False)
            if ret is not True:
                dis_errs["ps"] = f"PowerShell: {ret}"
        if enabled["wsl"]:
            ret = pm.set_wsl_proxy(old_h, old_p, False)
            if ret is not True:
                dis_errs["wsl"] = f"WSL: {ret}"
        for tgt, label, use_wsl in (("gitwin", "Git(Windows)", False),
                                    ("gitwsl", "Git(WSL)", True)):
            if enabled[tgt]:
                ret = pm.set_git_proxy(old_h, old_p, False, use_wsl=use_wsl)
                if ret is not True:
                    dis_errs[tgt] = f"{label}: {ret}"

        self.host, self.port = old_h, new_port
        with _cfg_lock:
            save_config(self._cfg_with_port(new_port))

        applied = []
        if not dis_errs:
            if enabled["wsl"]:
                pm._invalidate_wsl_network_mode_cache()
                ret = pm.set_wsl_proxy(old_h, new_port, True)
                if ret is True:
                    applied.append("wsl")
                else:
                    errs["wsl"] = f"WSL: {ret}"
            if enabled["ps"]:
                ret = pm.set_ps_proxy(old_h, new_port, True)
                if ret is True:
                    applied.append("ps")
                else:
                    errs["ps"] = f"PowerShell: {ret}"
            for tgt, label, use_wsl in (("gitwin", "Git(Windows)", False),
                                        ("gitwsl", "Git(WSL)", True)):
                if enabled[tgt]:
                    ret = pm.set_git_proxy(old_h, new_port, True, use_wsl=use_wsl)
                    if ret is True:
                        applied.append(tgt)
                        self._git_managed[tgt] = (old_h, new_port)
                    else:
                        errs[tgt] = f"{label}: {ret}"
            if "wsl" in applied and old_h.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式已自动改用宿主网关 IP。")
        else:
            errs = dis_errs
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
        while True:
            try:
                cur = pm.get_net_counters()
                with self._speed_lock:
                    up_str = dn_str = None
                    dn_num = 0.0
                    if cur is not None and self._speed_prev is not None:
                        up = cur[0] - self._speed_prev[0]
                        dn = cur[1] - self._speed_prev[1]
                        if 0 <= up <= 100 * 1024 * 1024:
                            up_str = pm.format_speed(up)
                        if 0 <= dn <= 100 * 1024 * 1024:
                            dn_str = pm.format_speed(dn)
                            dn_num = float(dn)
                    self._speed_prev = cur if cur is not None else self._speed_prev
                    alive = pm.proxy_port_alive(self.host, self.port) \
                        if (self.host and pm._is_valid_port(self.port)) else None
                    self._speed = {
                        "up": up_str or "—", "dn": dn_str or "—",
                        "dn_num": dn_num,
                        "iface": pm._primary_iface or "",
                        "alive": alive,
                    }
            except Exception:
                pass  # never let the sampler thread die
            time.sleep(1.0)

    def get_speed(self):
        with self._speed_lock:
            return dict(self._speed)

    # ── misc actions ──
    def restart_wsl(self):
        pm._invalidate_wsl_network_mode_cache()
        ret = pm.restart_wsl()
        # A WSL restart can move the NAT subnet; refresh the gateway IP baked
        # into the bashrc block when the proxy is on with a loopback host.
        if ret is True:
            wsl_state = self.refresh_item("wsl")
            if wsl_state and wsl_state.get("on") \
                    and self.host.lower() in pm.LOOPBACK_HOSTS \
                    and pm._is_valid_port(self.port) \
                    and pm.get_wsl_network_mode() != "Mirrored":
                pm.set_wsl_proxy(self.host, str(int(self.port)), True)
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

    # ── custom background ──
    def pick_background(self):
        """Native file picker via PowerShell (process-isolated, so no COM
        apartment headaches inside the pywebview process). stdout is forced
        to UTF-8 so Chinese paths survive the console round-trip."""
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
            self.save_config({"bg_path": path})
            return {"ok": True, "path": path, "name": os.path.basename(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
        elif cmd == "close":
            if self._tray_ok:
                w.hide()  # hide to tray, mirroring the tkinter app
            else:
                w.destroy()  # no tray: hiding would orphan the process
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
        maximize = resize to the monitor work area, restore = prev bounds."""
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
                l, t, r, b = self._prev_bounds
                user32.SetWindowPos(hwnd, 0, l, t, r - l, b - t, 0x0004)
            self._maximized = False
        set_resize_maximized(self._maximized)


def _make_tray(api, window):
    import pystray
    from PIL import Image

    def show(icon=None, item=None):
        window.show()

    def quit_app(icon=None, item=None):
        icon.stop()
        window.destroy()

    menu = pystray.Menu(
        pystray.MenuItem("显示主窗口", show, default=True),
        pystray.MenuItem("退出", quit_app),
    )
    icon = pystray.Icon("proxy_manager", Image.open(
        os.path.join(_BASE, "assets", "tray.png")).convert("RGBA"),
        "代理管理器", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


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
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        else:
            for h in _our_hwnds(other_pid=True):
                user32.ShowWindow(h, _SW_RESTORE)
                ctypes.windll.user32.SetForegroundWindow(h)
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

    window = webview.create_window(
        "代理管理器", UI_PATH,
        js_api=api, width=1060, height=700, min_size=(920, 620),
        frameless=FRAMELESS, easy_drag=False, resizable=True,
        background_color=bg, on_top=cfg.get("pin", False),
    )
    api._window = window

    # Tray icon lives for the whole process (like the tkinter build): the
    # frameless window has no native close button, and the X button hides to
    # tray, so the icon must exist before any hide — not only after a
    # FormClosed event, which hide() never raises anyway.
    try:
        _make_tray(api, window)
        api._tray_ok = True
    except Exception as e:
        print(f"[pm] tray unavailable: {e}", flush=True)

    # Native frameless resize: install the WM_NCHITTEST hook as soon as the
    # window exists, then keep re-installing via the watchdog below — WinForms
    # can recreate its handle at any time, which silently drops a subclass.
    def _on_loaded():
        ensure_resize_hook(window)
    window.events.loaded += _on_loaded

    # Alt+F4 on the frameless window should behave like the X button: hide to
    # tray (returning False cancels the native close). Tray 退出 still uses
    # destroy(), which bypasses this event entirely.
    def _on_closing():
        if api._tray_ok:
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
                    window.show()
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
