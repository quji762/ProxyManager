"""
Proxy Manager - One-click proxy injection for PowerShell & WSL
Landscape layout, light/dark themes, system tray.
"""

import customtkinter as ctk
import tkinter.messagebox as messagebox
import winreg
import subprocess
import re
import json
import os
import socket
import threading
import ctypes
import uuid
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

# pystray / PIL are only needed when the user hides to the tray, so we defer
# their import to _start_tray() to shave ~20ms off cold start.

# ── Crash logging ─────────────────────────────────────────────────────────────
# A --windowed PyInstaller build has no console, so unhandled exceptions in the
# UI thread would vanish silently. We funnel every crash path (the interpreter
# excepthook AND tkinter's _report_exception for callback failures) through one
# writer that appends a timestamped traceback to %APPDATA%\ProxyManager\crash.log.
_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
_CRASH_LOG = os.path.join(_APPDATA, "ProxyManager", "crash.log")

# ── Single-instance guard ─────────────────────────────────────────────────────
# A second launch writes a "show" signal and exits; the running instance polls
# for it and brings its window back.  This avoids invisible zombie processes
# that the user sees in Task Manager but can't interact with.
_SIGNAL_FILE = os.path.join(_APPDATA, "ProxyManager", "wake.signal")


def _log_crash(exc_type, exc_val, exc_tb):
    """Append a timestamped traceback to crash.log. Best-effort: never raises."""
    import traceback, datetime
    try:
        os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {exc_type.__name__}: {exc_val}\n")
            traceback.print_tb(exc_tb, file=f)
            f.write("\n")
    except Exception:
        pass


# ── Patch customtkinter _root() crash ─────────────────────────────────────────
# customtkinter 6.0.0 has a bug where, under PyInstaller (and occasionally under
# the interpreter), a widget's _root attribute gets overwritten with the root
# CTk instance instead of staying a bound method. When an event binding fires,
# tkinter's _substitute() calls nametowidget() -> w._root(), which then raises
# TypeError("'CTk' object is not callable"). Because _substitute only catches
# KeyError, the exception propagates into CallWrapper.__call__ ->
# _report_exception, which *also* calls self._root() and fails the same way,
# taking the whole app down.
#
# We harden both failure points so a bad _root is tolerated gracefully.
import tkinter

def _safe_nametowidget(self, name):
    """nametowidget that tolerates a corrupt _root attribute."""
    name = str(name).split('.')
    w = self
    if not name[0]:
        root = w._root
        if callable(root):
            w = root()
        else:
            # _root was overwritten with the root widget instance itself.
            w = root if root is not None else w
        name = name[1:]
    for n in name:
        if not n:
            break
        w = w.children[n]
    return w

def _safe_report_exception(self):
    """_report_exception that tolerates a corrupt _root attribute."""
    import sys, traceback
    exc, val, tb = sys.exc_info()
    # Persist UI-thread callback crashes (the stock path never reaches the
    # interpreter's sys.excepthook, so without this they'd be lost in a
    # --windowed build).
    _log_crash(exc, val, tb)
    try:
        root = self._root
        if callable(root):
            root = root()
        root.report_callback_exception(exc, val, tb)
    except Exception:
        # Last resort: dump to stderr so the process doesn't die silently.
        traceback.print_exception(exc, val, tb)

tkinter.Misc._nametowidget = _safe_nametowidget
tkinter.Misc.nametowidget = _safe_nametowidget
tkinter.Misc._report_exception = _safe_report_exception

user32 = ctypes.WinDLL("user32")

# Explicit prototype for SendMessageTimeoutW — without this, 64-bit Python
# truncates pointer-sized args (lParam string pointer) and the WM_SETTINGCHANGE
# broadcast silently fails, so new terminals don't pick up the env change.
user32.SendMessageTimeoutW.argtypes = [
    ctypes.c_void_p,                   # HWND       (HWND_BROADCAST = 0xFFFF)
    ctypes.c_uint,                     # Msg        (WM_SETTINGCHANGE = 0x001A)
    ctypes.c_size_t,                   # wParam     (0)
    ctypes.c_wchar_p,                  # lParam     ("Environment")
    ctypes.c_uint,                     # fuFlags    (SMTO_ABORTIFHUNG = 0x0002)
    ctypes.c_uint,                     # uTimeout   (ms)
    ctypes.POINTER(ctypes.c_long),     # lpdwResult
]
user32.SendMessageTimeoutW.restype = ctypes.c_long  # LPARAM

# Lightweight SendMessageW with integer wParam/lParam for WM_SETREDRAW.
user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_size_t, ctypes.c_size_t]
user32.SendMessageW.restype = ctypes.c_long

HWND_BROADCAST   = 0xFFFF
WM_SETTINGCHANGE = 0x001A
WM_SETREDRAW     = 0x000B

# ─── Theme palettes ───────────────────────────────────────────────────────────
# Two full color sets; C is the active one, swapped by apply_theme().
PALETTES = {
    "light": {
        "bg":       "#F2F1F6",   # lavender-tinted background
        "card":     "#FFFFFF",
        "accent":   "#5B7BE8",   # periwinkle blue
        "accent_h": "#4A6AD8",
        "green":    "#34C759",   # iOS-style green (status dot / copy feedback)
        "green_h":  "#28A745",
        "red":      "#D9605B",   # soft coral
        "red_h":    "#C2504B",
        "text":     "#2A2A2E",
        "text2":    "#9A9AA0",
        "border":   "#E4E4E8",
        "input_bg": "#F6F6F9",
        "secondary":    "#8A8A92",  # restart button
        "secondary_h":  "#6E6E76",
        "danger_hover": "#FCEDEA",  # outline disable button hover
    },
    "dark": {
        "bg":       "#16161A",
        "card":     "#222228",
        "accent":   "#5B7BE8",
        "accent_h": "#4A6AD8",
        "green":    "#34C759",
        "green_h":  "#28A745",
        "red":      "#D9605B",
        "red_h":    "#C2504B",
        "text":     "#E4E4EC",
        "text2":    "#888892",
        "border":   "#33333C",
        "input_bg": "#1B1B22",
        "secondary":    "#6E6E78",
        "secondary_h":  "#5A5A64",
        "danger_hover": "#2A1E1F",
    },
}

C = PALETTES["light"].copy()


def apply_theme(mode: str):
    """Swap the active palette and inform customtkinter."""
    global C
    C = PALETTES.get(mode, PALETTES["light"]).copy()
    ctk.set_appearance_mode(mode)


# ─── Constants ────────────────────────────────────────────────────────────────
APP_TITLE  = "代理管理器"
APP_WIDTH  = 760
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "7897"


def _config_dir() -> str:
    """Persist config under %APPDATA%\\ProxyManager so it survives PyInstaller
    --onefile runs (where __file__ points to a transient _MEIxxxxx dir)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ProxyManager")


CONFIG_FILE = os.path.join(_config_dir(), "config.json")

FONT_CN = "Microsoft YaHei UI"
FONT_EN = "Segoe UI"

WSL_PROXY_BEGIN = "# >>> proxy-manager begin >>>"
WSL_PROXY_END   = "# <<< proxy-manager end <<<"
ENV_REG_PATH    = r"Environment"
NO_PROXY_VALUE  = "localhost,127.0.0.1,::1"
LOOPBACK_HOSTS  = {"127.0.0.1", "localhost", "::1", "::"}


# ─── Config ───────────────────────────────────────────────────────────────────
def _is_valid_port(p: str) -> bool:
    """ASCII digits only — str.isdigit() also accepts superscripts etc. that
    int() then rejects."""
    return p.isascii() and p.isdigit() and 1 <= int(p) <= 65535


def load_config():
    """Read config.json defensively: on-disk values are never trusted, since a
    hand-edited or partially written file crashed startup (StringVar(int))."""
    defaults = {"host": DEFAULT_HOST, "port": DEFAULT_PORT,
                "powershell": True, "wsl": True, "pin": False, "mode": "light"}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return defaults
        if isinstance(cfg, dict):
            for k, dv in defaults.items():
                v = cfg.get(k, dv)
                if k == "host":
                    v = ("" if v is None else str(v)).strip() or dv
                elif k == "port":
                    v = str(v).strip()
                    if not _is_valid_port(v):
                        v = dv
                elif k == "mode":
                    v = str(v).lower()
                    if v not in PALETTES:
                        v = dv
                else:
                    v = bool(v)
                defaults[k] = v
    return defaults


def save_config(host, port, powershell, wsl, pin=False, mode="light"):
    try:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"host": host, "port": port, "powershell": powershell,
                       "wsl": wsl, "pin": pin, "mode": mode},
                      f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ─── Network Info ─────────────────────────────────────────────────────────────
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "未检测到"


def get_wsl_ip():
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c", "hostname -I 2>/dev/null | awk '{print $1}'"],
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
        ip = r.stdout.strip()
        return ip if ip else "未获取到"
    except Exception:
        return "WSL 未运行"


def get_wsl_ip_and_block():
    """Fetch WSL IP, the proxy-manager bashrc block, and the WSL git proxy in
    a single wsl.exe call.

    Each wsl.exe invocation costs ~5s of process-startup overhead on a cold
    WSL instance, so merging the commands saves ~10s of refresh latency.
    Returns (ip_str, block_str, git_proxy_str)."""
    try:
        # Generate random separators per call so they can never collide with
        # user content in ~/.bashrc (unlike a fixed string like "<<PM_SEP>>").
        sep1 = uuid.uuid4().hex
        sep2 = uuid.uuid4().hex
        cmd = (
            "hostname -I 2>/dev/null | awk '{print $1}';"
            f"echo '{sep1}';"
            f"sed -n '/{re.escape(WSL_PROXY_BEGIN)}/,/{re.escape(WSL_PROXY_END)}/p' ~/.bashrc 2>/dev/null;"
            f"echo '{sep2}';"
            "git config --global --get http.proxy 2>/dev/null"
        )
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c", cmd],
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
        out = r.stdout
        git_part = ""
        if sep2 in out:
            out, git_part = out.split(sep2, 1)
            git_part = git_part.strip()
        if sep1 in out:
            ip_part, block_part = out.split(sep1, 1)
            # strip() removes the trailing newline that `echo` appends, so an
            # empty bashrc block stays empty (falsy) - matching the semantics of
            # the original _read_wsl_block and keeping get_wsl_status's
            # `if not block: return None` guard working correctly.
            block_part = block_part.strip()
        else:
            ip_part, block_part = out, ""
        ip = ip_part.strip()
        ip = ip if ip else "未获取到"
        git_part = git_part if git_part else "未设置"
        return (ip, block_part, git_part)
    except Exception:
        return ("WSL 未运行", "", "未设置")


# Cache is keyed by .wslconfig's mtime so editing networkingMode and clicking
# refresh picks up the new value instead of returning a stale, never-expiring
# result (the original version cached indefinitely once first computed).
_wsl_network_mode_cache: Optional[tuple] = None  # (mtime, mode_str)


def _invalidate_wsl_network_mode_cache():
    """Drop the cached networkingMode so the next read re-parses .wslconfig.
    Called after a WSL restart, which is when a config change takes effect."""
    global _wsl_network_mode_cache
    _wsl_network_mode_cache = None


def get_wsl_network_mode():
    global _wsl_network_mode_cache
    try:
        p = os.path.expanduser(r"~\.wslconfig")
        mtime = os.path.getmtime(p) if os.path.exists(p) else None
        if _wsl_network_mode_cache is not None:
            cached_mtime, cached_mode = _wsl_network_mode_cache
            # Cache hit only if .wslconfig is unchanged since we parsed it.
            if cached_mtime == mtime:
                return cached_mode
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            # .wslconfig is INI-shaped: only honor networkingMode under [wsl2].
            in_wsl2 = False
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    in_wsl2 = (line.lower() == "[wsl2]")
                    continue
                if in_wsl2:
                    m = re.match(r"^networkingMode\s*=\s*(\S+)\s*$", line, re.IGNORECASE)
                    if m:
                        mode = m.group(1).capitalize()
                        _wsl_network_mode_cache = (mtime, mode)
                        return mode
        _wsl_network_mode_cache = (mtime, "默认(NAT)")
        return _wsl_network_mode_cache[1]
    except Exception:
        return "未知"


# ─── Network Speed (optional, psutil) ─────────────────────────────────────────
# We track a single "primary" physical interface rather than the system-wide
# total, so virtual adapters (WSL, Docker, Tailscale, VPN loops) don't inflate
# the reading with background chatter the user never notices.
_primary_iface = None  # cached name of the interface we decided to track


def _pick_primary_iface() -> Optional[str]:
    """Choose the best interface to monitor: the one that is up, carries an
    IP, and is not a obvious virtual/loopback adapter. Returns None if psutil
    is unavailable or no suitable interface exists."""
    if not HAVE_PSUTIL:
        return None
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
    except Exception:
        return None

    def is_virtual(name: str) -> bool:
        n = name.lower()
        return any(kw in n for kw in ("loopback", "tap-", "tun-", "veth",
                                        "docker", "hyper-v", "virtual",
                                        "pseudo", "tailscale", "zerotier",
                                        "wireguard", "vpn"))

    candidates = []
    for name, st in stats.items():
        if not st.isup:
            continue
        if is_virtual(name):
            continue
        # Require at least one IPv4 address (skip purely link-local / disconnected).
        iface_addrs = addrs.get(name, [])
        has_ip = any(a.family == socket.AF_INET for a in iface_addrs)
        if has_ip:
            candidates.append((name, st.speed))

    if not candidates:
        return None
    # Prefer the fastest interface (wired > wireless); fall back to first.
    candidates.sort(key=lambda c: c[1], reverse=True)
    return candidates[0][0]


def get_net_counters():
    """Return (bytes_sent, bytes_recv) snapshot for the primary interface,
    or None if unavailable. On the first call the interface is auto-selected."""
    global _primary_iface
    if not HAVE_PSUTIL:
        return None
    try:
        if _primary_iface is None:
            _primary_iface = _pick_primary_iface()
        if _primary_iface is None:
            # No suitable interface; fall back to system-wide counters.
            io = psutil.net_io_counters()
        else:
            per = psutil.net_io_counters(pernic=True)
            io = per.get(_primary_iface)
            if io is None:
                # Interface disappeared; re-pick next time.
                _primary_iface = None
                return None
        return (io.bytes_sent, io.bytes_recv)
    except Exception:
        return None


def format_speed(bytes_per_sec: float) -> str:
    """Human-readable rate, e.g. 1.2 MB/s."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    val = bytes_per_sec
    label = "KB"
    for lbl, factor in (("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3)):
        if bytes_per_sec >= factor:
            val = bytes_per_sec / factor
            label = lbl
        else:
            break
    return f"{val:.1f} {label}/s"


# ─── Proxy Logic ──────────────────────────────────────────────────────────────
def get_ps_status():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0, winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, "HTTP_PROXY")
            winreg.CloseKey(key)
            return v
        except FileNotFoundError:
            winreg.CloseKey(key)
            return None
    except OSError:
        return None


def get_ps_proxy_vars():
    """Read HTTP_PROXY and HTTPS_PROXY from Windows user env."""
    result = {"HTTP_PROXY": "未设置", "HTTPS_PROXY": "未设置"}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0, winreg.KEY_READ)
        for name in ("HTTP_PROXY", "HTTPS_PROXY"):
            try:
                v, _ = winreg.QueryValueEx(key, name)
                result[name] = v
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except OSError:
        pass
    return result


def _read_wsl_block() -> str:
    """Return the text between the proxy-manager markers in ~/.bashrc (empty
    if absent). Scoping reads to this block avoids mis-picking up commented or
    user-written export lines elsewhere in the file."""
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c",
             f"sed -n '/{re.escape(WSL_PROXY_BEGIN)}/,/{re.escape(WSL_PROXY_END)}/p' ~/.bashrc 2>/dev/null"],
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
        return r.stdout
    except Exception:
        return ""


def _parse_export(block: str, name: str):
    """Pull the value of `export NAME="..."` from the marker block."""
    m = re.search(
        r'^export\s+' + re.escape(name) + r'\s*=\s*["\']?([^"\'\n]+)["\']?\s*$',
        block, re.MULTILINE,
    )
    return m.group(1).strip() if m else None


def get_wsl_proxy_vars(block: Optional[str] = None):
    """Read HTTP_PROXY and HTTPS_PROXY produced by this tool from WSL.
    Pass an existing block (from _read_wsl_block) to avoid a redundant WSL
    subprocess call."""
    result = {"HTTP_PROXY": "未设置", "HTTPS_PROXY": "未设置"}
    if block is None:
        block = _read_wsl_block()
    if not block:
        return result
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        val = _parse_export(block, name)
        if val:
            result[name] = val
    return result


def get_wsl_status(block: Optional[str] = None):
    if block is None:
        block = _read_wsl_block()
    if not block:
        return None
    url = _parse_export(block, "HTTP_PROXY")
    return url if url else "configured"


def set_ps_proxy(host, port, enable=True):
    url = f"http://{host}:{port}"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_READ)
        if enable:
            winreg.SetValueEx(key, "HTTP_PROXY", 0, winreg.REG_SZ, url)
            winreg.SetValueEx(key, "HTTPS_PROXY", 0, winreg.REG_SZ, url)
            # Keep loopback traffic direct: without NO_PROXY, local dev servers
            # get routed through the upstream proxy and typically break.
            winreg.SetValueEx(key, "NO_PROXY", 0, winreg.REG_SZ, NO_PROXY_VALUE)
        else:
            for n in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
                try:
                    winreg.DeleteValue(key, n)
                except FileNotFoundError:
                    pass
        winreg.CloseKey(key)
        # Broadcast env change so already-running processes pick it up.
        ret = ctypes.c_long()
        user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            0x0002, 5000, ctypes.byref(ret),
        )
        return True
    except OSError as e:
        return str(e)


def _get_wsl_gateway_ip():
    """Return the WSL default gateway (= the Windows host as seen from WSL2
    NAT mode), or None if it cannot be determined (WSL down, WSL1, etc.)."""
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c",
             "ip route show default 2>/dev/null | awk '/default/ {print $3; exit}'"],
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
        ip = r.stdout.strip()
        if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", ip):
            return ip
    except Exception:
        pass
    return None


def set_wsl_proxy(host, port, enable=True):
    url = f"http://{host}:{port}"
    # In WSL2 NAT mode 127.0.0.1 inside WSL does not reach the Windows host
    # (localhost forwarding is Windows->WSL only), so a loopback host would
    # silently produce a dead proxy. Substitute the host's gateway IP; in
    # mirrored mode (and WSL1) loopback works and is left untouched.
    if enable and host.lower() in LOOPBACK_HOSTS and get_wsl_network_mode() != "Mirrored":
        gw = _get_wsl_gateway_ip()
        if gw:
            url = f"http://{gw}:{port}"
    block = f"""{WSL_PROXY_BEGIN}
export HTTP_PROXY="{url}"
export HTTPS_PROXY="{url}"
export http_proxy="{url}"
export https_proxy="{url}"
export NO_PROXY="{NO_PROXY_VALUE}"
export no_proxy="$NO_PROXY"
{WSL_PROXY_END}"""
    try:
        # Always remove any prior managed block first (idempotent).
        sed = f"sed -i '/{re.escape(WSL_PROXY_BEGIN)}/,/{re.escape(WSL_PROXY_END)}/d' ~/.bashrc"
        subprocess.run(["wsl", "-e", "bash", "-c", sed],
                       capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if enable:
            # Feed the block via stdin so no shell interpolation touches it —
            # avoids quoting/escaping pitfalls with arbitrary host strings.
            # Guard first: if ~/.bashrc lacks a trailing newline, the appended
            # marker glues onto the last user line and the later range-delete
            # would remove that user line along with the block.
            append = ("[ -s ~/.bashrc ] && [ -n \"$(tail -c1 ~/.bashrc)\" ]"
                      " && printf '\\n' >> ~/.bashrc; cat >> ~/.bashrc")
            # Send raw UTF-8 bytes: text=True would translate \n to \r\n on
            # Windows, leaving a literal \r in every bashrc line and polluting
            # the exported proxy values.
            subprocess.run(["wsl", "-e", "bash", "-c", append],
                           input=(block + "\n").encode("utf-8"),
                           capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception as e:
        # Disabling is best-effort cleanup: a missing/broken WSL has nothing
        # to clean, and reporting it would block the whole disable flow.
        if not enable:
            return True
        return str(e)


def set_git_proxy(host, port, enable=True, use_wsl=False):
    """Configure git's global http.proxy/https.proxy, either on Windows
    (use_wsl=False) or inside WSL (use_wsl=True).

    Host/port are pre-validated by the UI, so the URL is injection-safe.
    Disabling is best-effort: a missing git binary (or missing WSL) simply
    means there is nothing to clean up and is reported as success, so it
    never blocks the unconditional disable flow."""
    url = f"http://{host}:{port}"
    try:
        if use_wsl:
            if enable:
                cmd = (f"git config --global --replace-all http.proxy '{url}' && "
                       f"git config --global --replace-all https.proxy '{url}'")
            else:
                cmd = ("git config --global --unset-all http.proxy; "
                       "git config --global --unset-all https.proxy; true")
            r = subprocess.run(["wsl", "-e", "bash", "-c", cmd],
                               capture_output=True, text=True, timeout=15,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            if enable and r.returncode != 0:
                if "command not found" in (r.stderr or "") or r.returncode == 127:
                    return "WSL 内未找到 git"
                return (r.stderr or f"exit {r.returncode}").strip() or f"exit {r.returncode}"
        else:
            if enable:
                for key in ("http.proxy", "https.proxy"):
                    r = subprocess.run(["git", "config", "--global", "--replace-all", key, url],
                                       capture_output=True, text=True, timeout=15,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    if r.returncode != 0:
                        return (r.stderr or f"exit {r.returncode}").strip() or f"exit {r.returncode}"
            else:
                for key in ("http.proxy", "https.proxy"):
                    subprocess.run(["git", "config", "--global", "--unset-all", key],
                                   capture_output=True, timeout=15,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except FileNotFoundError:
        # Windows: git not on PATH. WSL: wsl.exe missing entirely.
        if not enable:
            return True
        return "WSL 内未找到 git" if use_wsl else "未找到 git，请确认已安装 Git"
    except Exception as e:
        if not enable:
            return True
        return str(e)


def get_git_proxy_win():
    """Read git's global http.proxy on Windows ('未设置' when unset, '未检测到'
    when git is unavailable)."""
    try:
        r = subprocess.run(["git", "config", "--global", "--get", "http.proxy"],
                           capture_output=True, text=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        v = r.stdout.strip()
        return v if v else "未设置"
    except Exception:
        return "未检测到"


def restart_wsl():
    try:
        subprocess.run(["wsl", "--shutdown"],
                       capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["wsl", "-e", "echo", "ok"],
                       capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception as e:
        return str(e)


# ─── Tray icon ────────────────────────────────────────────────────────────────
_TRAY_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "tray.png")


def _make_tray_image():
    """Load the tray icon from assets/tray.png, falling back to a programmatic
    icon if the file is missing (e.g. when running from a PyInstaller bundle
    where the file wasn't bundled)."""
    from PIL import Image, ImageDraw
    if os.path.isfile(_TRAY_ICON_PATH):
        return Image.open(_TRAY_ICON_PATH).convert("RGBA")
    # Fallback: draw a simple blue rounded-rect with white arrows.
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 63, 63], radius=13, fill=(91, 123, 232, 255))
    d.arc([10, 10, 54, 54], start=-30, end=150, fill=(255, 255, 255, 255), width=5)
    d.arc([10, 10, 54, 54], start=150, end=330, fill=(255, 255, 255, 255), width=5)
    return img


# ─── Info Row Widget ──────────────────────────────────────────────────────────
class InfoRow(ctk.CTkFrame):
    def __init__(self, master, label, value="", copyable=False, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._copyable = copyable
        self._root = self.winfo_toplevel()
        ctk.CTkLabel(self, text=label,
                     font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C["text2"], width=85, anchor="w"
                     ).pack(side="left")
        self._val = ctk.CTkLabel(self, text=value,
                                 font=ctk.CTkFont(family=FONT_EN, size=11, weight="bold"),
                                 text_color=C["text"], anchor="w")
        self._val.pack(side="left", fill="x", expand=True)
        if copyable:
            self._val.configure(cursor="hand2")
            self._orig_text = None
            for w in (self, self._val):
                w.bind("<Button-1>", self._on_copy)

    def set_value(self, t):
        self._val.configure(text=t)

    def _on_copy(self, _event=None):
        if not self._copyable:
            return
        text = self._val.cget("text")
        # If we're showing the "✓ 已复制" feedback, use the cached original
        # value so the user can re-copy by clicking again during the 1200ms
        # feedback window instead of being silently ignored.
        if self._orig_text is not None and "已复制" in text:
            text = self._orig_text
        # Skip placeholders / failure strings - only copy things that look
        # like an address (contains a digit, no spaces).
        if not text or any(c.isspace() for c in text) or not any(c.isdigit() for c in text):
            return
        try:
            self._root.clipboard_clear()
            self._root.clipboard_append(text)
        except Exception:
            return
        self._orig_text = text
        # Brief visual confirmation.
        self._val.configure(text=f"{text}  ✓ 已复制", text_color=C["green"])
        # Use the root-level after (not _val.after) so the callback is on the
        # top-level widget and survives InfoRow destruction in a rebuild.
        def restore():
            # Only restore if the feedback is still on screen - a concurrent
            # refresh (set_value) may have already updated the value, and
            # overwriting that fresh data with the copied text would show a
            # stale address.
            if "已复制" in self._val.cget("text"):
                self._val.configure(text=text, text_color=C["text"])
        self._root.after(1200, restore)


# ─── Main App ─────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)

        cfg = load_config()
        self.host_var = ctk.StringVar(value=cfg["host"])
        self.port_var = ctk.StringVar(value=cfg["port"])
        self.ps_var   = ctk.BooleanVar(value=cfg["powershell"])
        self.wsl_var  = ctk.BooleanVar(value=cfg["wsl"])
        self.pin_var  = ctk.BooleanVar(value=cfg.get("pin", False))

        # Apply persisted theme before building any widgets.
        self._mode = cfg.get("mode", "light")
        apply_theme(self._mode)
        self.root.configure(fg_color=C["bg"])

        # Tray state.
        self._tray_icon = None
        self._tray_thread = None

        # Last-known proxy on/off; lets a theme switch re-tint the status dot,
        # which the recursive restyle would otherwise reset to idle gray.
        self._proxy_on = False
        self._ps_on = False
        self._wsl_on = False
        self._refresh_token = None

        # Rebuild generation - incremented each time _build() tears down and
        # recreates the content tree. Callbacks scheduled via self._after() embed
        # the generation at creation time and no-op if a rebuild has since
        # happened, avoiding "CTk object is not callable" crashes that occur when
        # a stale after(0, …) fires against a destroyed widget (common with
        # customtkinter + PyInstaller).
        self._generation = 0

        self._build()

        # Apply persisted topmost state.
        self.root.attributes("-topmost", self.pin_var.get())

        # Auto-size height to fit content, cap at 85% screen height.
        self.root.update_idletasks()
        req_h = self.root.winfo_reqheight()
        max_h = int(self.root.winfo_screenheight() * 0.85)
        final_h = min(req_h + 10, max_h)
        x = (self.root.winfo_screenwidth() - APP_WIDTH) // 2
        y = (self.root.winfo_screenheight() - final_h) // 2
        self.root.geometry(f"{APP_WIDTH}x{final_h}+{x}+{y}")
        # Fixed size: width is constant and height auto-fits content, so there's
        # nothing to gain from resizing — and freeing the window from resize
        # eliminates the customtkinter <Configure> scaling overhead (which
        # fires minsize/maxsize/geometry on every pixel of drag).
        self.root.resizable(False, False)

        self._async_refresh()
        self._speed_prev = get_net_counters()
        if HAVE_PSUTIL:
            self._tick_speed()

        # Closing the window hides to the tray instead of quitting.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Show tray icon immediately on startup (not just when window is closed).
        self._start_tray()

    # ── Safe after() ─────────────────────────────────────────────────────────────
    def _after(self, delay_ms, func):
        """Schedule func on the main loop, but only run it if no rebuild has
        happened since scheduling. Prevents stale callbacks from touching
        destroyed widgets."""
        gen = self._generation
        def wrapper():
            if gen == self._generation:
                func()
        try:
            self.root.after(delay_ms, wrapper)
        except RuntimeError:
            # mainloop already terminated (window closed mid-refresh); drop
            # the callback silently instead of crashing the worker thread.
            pass

    # ── Build UI (two-column landscape) ──
    def _build(self):
        # Tear down previous content if rebuilding for a theme switch.
        for w in self.root.winfo_children():
            if getattr(w, "_is_content", False):
                w.destroy()

        # Bump generation so any pending callbacks from the previous UI tree
        # become no-ops instead of operating on destroyed widgets.
        self._generation += 1

        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content._is_content = True
        content.pack(fill="both", expand=True, padx=0, pady=0)
        content.grid_columnconfigure(0, weight=1, uniform="col")
        content.grid_columnconfigure(1, weight=1, uniform="col")
        content.grid_rowconfigure(1, weight=1)

        self._build_title(content)
        self._build_columns(content)
        self._build_buttons(content)

    def _build_title(self, parent):
        tf = ctk.CTkFrame(parent, fg_color="transparent")
        tf.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 4))
        tf.grid_columnconfigure(0, weight=1)

        # Left: app name + subtitle.
        left = ctk.CTkFrame(tf, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="代理管理器",
                     font=ctk.CTkFont(family=FONT_CN, size=19, weight="bold"),
                     text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(left, text="PowerShell · Git · WSL 一键代理切换",
                     font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C["text2"]).pack(anchor="w")

        # Right: theme toggle, pin, refresh.
        right = ctk.CTkFrame(tf, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")

        self._theme_btn = ctk.CTkButton(
            right, text=self._theme_icon(), width=34, height=34,
            font=ctk.CTkFont(size=15), corner_radius=17,
            fg_color=C["input_bg"], hover_color=C["border"],
            text_color=C["text2"], command=self._toggle_theme)
        self._theme_btn.pack(side="left", padx=(0, 6))

        self._pin_btn = ctk.CTkButton(
            right, text="📌", width=34, height=34,
            font=ctk.CTkFont(size=14), corner_radius=17,
            fg_color=C["input_bg"], hover_color=C["border"],
            text_color=C["text2"], command=self._toggle_pin)
        self._pin_btn.pack(side="left", padx=(0, 6))
        self._sync_pin_btn()

        self._refresh = ctk.CTkButton(
            right, text="⟳", width=34, height=34,
            font=ctk.CTkFont(size=16), corner_radius=17,
            fg_color=C["input_bg"], hover_color=C["border"],
            text_color=C["text2"], command=self._async_refresh)
        self._refresh.pack(side="left")

    def _build_columns(self, parent):
        # Shared padding dict for cards.
        S = {"padx": 16, "pady": (0, 10)}

        # ── Left column: status + network info ──
        left = ctk.CTkFrame(parent, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=4)

        # Status card.
        sc = ctk.CTkFrame(left, corner_radius=14, fg_color=C["card"],
                          border_width=1, border_color=C["border"])
        sc.pack(fill="x", **S)
        si = ctk.CTkFrame(sc, fg_color="transparent")
        si.pack(fill="x", padx=16, pady=14)
        self._dot = ctk.CTkLabel(si, text="●", font=ctk.CTkFont(size=26),
                                 text_color=C["text2"], width=36)
        self._dot.pack(side="left")
        stf = ctk.CTkFrame(si, fg_color="transparent")
        stf.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(stf, text="代理状态",
                     font=ctk.CTkFont(family=FONT_CN, size=10),
                     text_color=C["text2"]).pack(anchor="w")
        self._status = ctk.CTkLabel(stf, text="检测中…",
                                    font=ctk.CTkFont(family=FONT_CN, size=15, weight="bold"),
                                    text_color=C["text"])
        self._status.pack(anchor="w")

        # Network info card.
        nc = ctk.CTkFrame(left, corner_radius=14, fg_color=C["card"],
                          border_width=1, border_color=C["border"])
        nc.pack(fill="x", **S)
        ctk.CTkLabel(nc, text="网络信息",
                     font=ctk.CTkFont(family=FONT_CN, size=12, weight="bold"),
                     text_color=C["text"]).pack(anchor="w", padx=16, pady=(12, 6))
        self._lip = InfoRow(nc, "本机 IP", copyable=True)
        self._lip.pack(fill="x", padx=16, pady=(0, 2))
        self._wip = InfoRow(nc, "WSL IP", copyable=True)
        self._wip.pack(fill="x", padx=16, pady=(0, 2))
        self._wmode = InfoRow(nc, "WSL 网络模式")
        self._wmode.pack(fill="x", padx=16, pady=(0, 2))

        # Live speed (only when psutil is available).
        self._speed_up = self._speed_dn = self._speed_hdr = None
        if HAVE_PSUTIL:
            self._speed_hdr = ctk.CTkLabel(nc, text=self._speed_title(),
                                           font=ctk.CTkFont(family=FONT_CN, size=11, weight="bold"),
                                           text_color=C["text2"])
            self._speed_hdr._tint = "text2"
            self._speed_hdr.pack(anchor="w", padx=16, pady=(6, 2))
            self._speed_up = InfoRow(nc, "↑ 上传", "0 B/s")
            self._speed_up.pack(fill="x", padx=16, pady=(0, 2))
            self._speed_dn = InfoRow(nc, "↓ 下载", "0 B/s")
            self._speed_dn.pack(fill="x", padx=16, pady=(0, 12))
        else:
            # Keep card bottom spacing consistent.
            ctk.CTkLabel(nc, text="", height=12).pack()

        # ── Right column: proxy variables + config ──
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=4)

        # Proxy variables card.
        pc = ctk.CTkFrame(right, corner_radius=14, fg_color=C["card"],
                          border_width=1, border_color=C["border"])
        pc.pack(fill="x", **S)
        ctk.CTkLabel(pc, text="代理变量",
                     font=ctk.CTkFont(family=FONT_CN, size=12, weight="bold"),
                     text_color=C["text"]).pack(anchor="w", padx=16, pady=(12, 6))
        ps_hd = ctk.CTkLabel(pc, text="PowerShell（用户环境变量）",
                             font=ctk.CTkFont(family=FONT_EN, size=10, weight="bold"),
                             text_color=C["text2"])
        ps_hd._tint = "text2"  # pinned secondary color across theme switches
        ps_hd.pack(anchor="w", padx=16, pady=(0, 2))
        self._ps_http = InfoRow(pc, "HTTP_PROXY")
        self._ps_http.pack(fill="x", padx=16, pady=(0, 2))
        self._ps_https = InfoRow(pc, "HTTPS_PROXY")
        self._ps_https.pack(fill="x", padx=16, pady=(0, 2))
        self._git_http = InfoRow(pc, "Git 代理")
        self._git_http.pack(fill="x", padx=16, pady=(0, 8))
        wsl_hd = ctk.CTkLabel(pc, text="WSL",
                              font=ctk.CTkFont(family=FONT_EN, size=10, weight="bold"),
                              text_color=C["text2"])
        wsl_hd._tint = "text2"
        wsl_hd.pack(anchor="w", padx=16, pady=(0, 2))
        self._wsl_http = InfoRow(pc, "HTTP_PROXY")
        self._wsl_http.pack(fill="x", padx=16, pady=(0, 2))
        self._wsl_https = InfoRow(pc, "HTTPS_PROXY")
        self._wsl_https.pack(fill="x", padx=16, pady=(0, 2))
        self._wsl_git = InfoRow(pc, "Git 代理")
        self._wsl_git.pack(fill="x", padx=16, pady=(0, 12))

        # Config card.
        cc = ctk.CTkFrame(right, corner_radius=14, fg_color=C["card"],
                          border_width=1, border_color=C["border"])
        cc.pack(fill="x", **S)
        ctk.CTkLabel(cc, text="代理配置",
                     font=ctk.CTkFont(family=FONT_CN, size=12, weight="bold"),
                     text_color=C["text"]).pack(anchor="w", padx=16, pady=(12, 6))

        hf = ctk.CTkFrame(cc, fg_color="transparent")
        hf.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(hf, text="地址", font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C["text2"], width=36).pack(side="left")
        self._host = ctk.CTkEntry(hf, textvariable=self.host_var,
                                  font=ctk.CTkFont(family=FONT_EN, size=13),
                                  corner_radius=10, height=36, border_width=1,
                                  border_color=C["border"], fg_color=C["input_bg"])
        self._host.pack(side="left", fill="x", expand=True, padx=(8, 0))

        pf = ctk.CTkFrame(cc, fg_color="transparent")
        pf.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(pf, text="端口", font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C["text2"], width=36).pack(side="left")
        self._port = ctk.CTkEntry(pf, textvariable=self.port_var,
                                  font=ctk.CTkFont(family=FONT_EN, size=13),
                                  corner_radius=10, height=36, border_width=1,
                                  border_color=C["border"], fg_color=C["input_bg"], width=110)
        self._port.pack(side="left", padx=(8, 0))

        sw = ctk.CTkFrame(cc, fg_color="transparent")
        sw.pack(fill="x", padx=16, pady=(0, 6))
        self._ps_sw = ctk.CTkSwitch(sw, text="PowerShell", variable=self.ps_var,
                                    command=self._toggle_ps,
                                    font=ctk.CTkFont(family=FONT_CN, size=12),
                                    progress_color=C["accent"],
                                    button_color=C["text2"],
                                    button_hover_color=C["text"])
        self._ps_sw.pack(side="left")
        self._wsl_sw = ctk.CTkSwitch(sw, text="WSL", variable=self.wsl_var,
                                     command=self._toggle_wsl,
                                     font=ctk.CTkFont(family=FONT_CN, size=12),
                                     progress_color=C["accent"],
                                     button_color=C["text2"],
                                     button_hover_color=C["text"])
        self._wsl_sw.pack(side="left", padx=(24, 0))
        ctk.CTkLabel(cc, text="开关即启用/停用对应终端的代理（含该端的 git 配置），新开终端生效。\n"
                              "提示：Windows PowerShell 5.1 不读取环境变量，代理对 pwsh、git、curl 等生效。",
                     font=ctk.CTkFont(family=FONT_CN, size=10),
                     text_color=C["text2"], justify="left").pack(anchor="w", padx=16, pady=(0, 12))

    def _build_buttons(self, parent):
        bf = ctk.CTkFrame(parent, fg_color="transparent")
        bf.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 12))
        bf.grid_columnconfigure(0, weight=1)

        self._rst = ctk.CTkButton(
            bf, text="重启 WSL", height=42,
            font=ctk.CTkFont(family=FONT_CN, size=14, weight="bold"),
            corner_radius=12, fg_color=C["secondary"], hover_color=C["secondary_h"],
            text_color="#FFFFFF", command=self._restart)
        self._rst._role = "secondary"  # stable identifier for theme restyling
        self._rst.grid(row=0, column=0, sticky="ew")

    # ── Async Refresh ──
    def _async_refresh(self):
        self._refresh.configure(state="disabled")
        self._theme_btn.configure(state="disabled")
        # Lock the switches too: a toggle mid-refresh would stack a second
        # _async_refresh via _toggle_done, wasting a ~5s WSL call.
        self._lock_actions(True)
        self._status.configure(text="检测中…")
        self._dot.configure(text_color=C["text2"])

        # Token guards UI callbacks against stale refreshes: if a second
        # _async_refresh starts before the first's futures all settle, the old
        # callbacks see a mismatched token and no-op instead of overwriting the
        # new refresh's _ps_on/_wsl_on with stale data.
        token = object()
        self._refresh_token = token

        # Incremental refresh: each future updates its UI row as soon as it
        # completes, so the user sees local info (~1ms) immediately instead of
        # staring at "检测中…" for 5-6s while WSL spins up. The refresh button
        # is re-enabled only after every future has settled.
        pending = {"n": 6}  # one per future (ps, li, mo, psp, git, wsl)

        def on_done(_future):
            # Decrement in the main thread to avoid races on pending["n"].
            self._after(0, _finish)

        def _finish():
            pending["n"] -= 1
            if pending["n"] <= 0:
                self._refresh.configure(state="normal")
                self._theme_btn.configure(state="normal")
                self._lock_actions(False)

        # Track PS and WSL proxy states independently so that whichever future
        # completes first cannot clobber the other's contribution. Both callbacks
        # funnel through _sync_status which ORs the two into _proxy_on.
        self._ps_on = False
        self._wsl_on = False

        def _sync_status():
            self._proxy_on = bool(self._ps_on or self._wsl_on)
            if self._proxy_on:
                self._dot.configure(text_color=C["green"])
                self._status.configure(text="已启用")
            else:
                self._dot.configure(text_color=C["text2"])
                self._status.configure(text="未启用")

        def ui_ps(ps):
            if self._refresh_token is not token:
                return
            self._ps_on = bool(ps)
            # The switch mirrors the actually detected state, so a failed or
            # externally undone toggle snaps back instead of lying.
            self.ps_var.set(self._ps_on)
            _sync_status()

        def ui_ps_vars(psp):
            if self._refresh_token is not token:
                return
            self._ps_http.set_value(psp.get("HTTP_PROXY", "未设置"))
            self._ps_https.set_value(psp.get("HTTPS_PROXY", "未设置"))

        def ui_git_win(gv):
            if self._refresh_token is not token:
                return
            self._git_http.set_value(gv)

        def ui_li(li):
            if self._refresh_token is not token:
                return
            self._lip.set_value(li)

        def ui_mo(mo):
            if self._refresh_token is not token:
                return
            self._wmode.set_value(mo)

        def ui_wsl(ip, ws, wsp, wgit):
            """Update WSL rows. Parsing (get_wsl_status/get_wsl_proxy_vars) is
            done in the callback so the regex runs on the pool thread, not the
            UI thread."""
            if self._refresh_token is not token:
                return
            self._wip.set_value(ip)
            self._wsl_http.set_value(wsp.get("HTTP_PROXY", "未设置"))
            self._wsl_https.set_value(wsp.get("HTTPS_PROXY", "未设置"))
            self._wsl_git.set_value(wgit)
            self._wsl_on = bool(ws)
            self.wsl_var.set(self._wsl_on)
            _sync_status()

        def work():
            with ThreadPoolExecutor(max_workers=6) as pool:
                f_ps  = pool.submit(get_ps_status)
                f_li  = pool.submit(get_local_ip)
                f_mo  = pool.submit(get_wsl_network_mode)
                f_psp = pool.submit(get_ps_proxy_vars)
                f_git = pool.submit(get_git_proxy_win)
                # Merge three WSL reads (get_wsl_ip + _read_wsl_block + WSL git
                # proxy) into one wsl.exe invocation to save ~10s of latency.
                f_wsl = pool.submit(get_wsl_ip_and_block)

                f_ps.add_done_callback(lambda f: self._after(0, lambda: ui_ps(f.result())))
                f_li.add_done_callback(lambda f: self._after(0, lambda: ui_li(f.result())))
                f_mo.add_done_callback(lambda f: self._after(0, lambda: ui_mo(f.result())))
                f_psp.add_done_callback(lambda f: self._after(0, lambda: ui_ps_vars(f.result())))
                f_git.add_done_callback(lambda f: self._after(0, lambda: ui_git_win(f.result())))
                # Parse the block on the pool thread, then marshal only the
                # pre-computed values to the UI thread.
                def wsl_cb(f):
                    ip, block, wgit = f.result()
                    ws = get_wsl_status(block)
                    wsp = get_wsl_proxy_vars(block)
                    self._after(0, lambda: ui_wsl(ip, ws, wsp, wgit))
                f_wsl.add_done_callback(wsl_cb)

                for f in (f_ps, f_li, f_mo, f_psp, f_git, f_wsl):
                    f.add_done_callback(on_done)

        threading.Thread(target=work, daemon=True).start()

    # ── Validate ──
    def _validate(self):
        h = self.host_var.get().strip()
        p = self.port_var.get().strip()
        if not h:
            messagebox.showwarning("提示", "请填写代理地址")
            return None
        if any(c.isspace() for c in h):
            messagebox.showwarning("提示", "代理地址不能包含空格")
            return None
        # Reject shell metacharacters that could break the export line written
        # to ~/.bashrc (quotes, semicolons, dollar, backtick, pipe, ampersand).
        # ':' is excluded too: the proxy URL is assembled as http://host:port,
        # so a colon in host almost always means the user pasted "host:port"
        # here by mistake instead of using the dedicated port field.
        _ALLOWED = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789.-_"
        )
        if not all(c in _ALLOWED for c in h):
            if ":" in h:
                messagebox.showwarning("提示", "地址中不应包含端口或冒号。\n端口请填入下方的“端口”字段。")
            else:
                messagebox.showwarning("提示", "地址含非法字符（仅允许字母、数字、. - _）")
            return None
        # Warn if the user pasted a full URL.
        if "://" in h:
            messagebox.showwarning("提示", "只需填写地址，不含协议头（如 http://）")
            return None
        if not _is_valid_port(p):
            messagebox.showwarning("提示", "端口须为 1-65535 的整数")
            return None
        return h, str(int(p))

    # ── Action locking (shared by switches and restart) ──
    def _lock_actions(self, locked: bool, busy_btn=None, busy_label: str = ""):
        """Freeze the switches and the restart button during background work
        so nothing re-enters mid-operation; busy_btn doubles as the progress
        indicator."""
        for btn, idle in ((self._rst, "重启 WSL"),):
            text = busy_label if (locked and btn is busy_btn) else idle
            btn.configure(state="disabled" if locked else "normal", text=text)
        for sw in (self._ps_sw, self._wsl_sw):
            sw.configure(state="disabled" if locked else "normal")

    def _toggle_done(self, errs, label, note=None):
        """Common completion path for both terminal switches."""
        self._lock_actions(False)
        if errs:
            messagebox.showerror("错误", f"{label}: " + "\n".join(errs))
        elif note:
            messagebox.showinfo("提示", note)
        else:
            # Persist only on success, so the config never claims a state the
            # system wasn't actually put into.
            save_config(self.host_var.get().strip(), self.port_var.get().strip(),
                        self.ps_var.get(), self.wsl_var.get(),
                        self.pin_var.get(), self._mode)
        # On error the refresh re-syncs each switch to the actually detected
        # state, so a failed toggle snaps back instead of lying.
        self._async_refresh()

    # ── Per-terminal switches ──
    # Each switch applies/removes that terminal's proxy (env vars / bashrc
    # block) AND the git config on the same side, so a terminal's switch is
    # the single source of truth for its whole proxy state.
    def _toggle_ps(self):
        on = bool(self.ps_var.get())
        h = p = ""
        if on:
            r = self._validate()
            if not r:
                self._ps_sw.deselect()
                return
            h, p = r
        self._lock_actions(True)

        def work():
            errs = []
            for fn in (lambda: set_ps_proxy(h, p, on),
                       lambda: set_git_proxy(h, p, on, use_wsl=False)):
                ret = fn()
                if ret is not True:
                    errs.append(str(ret))
            self._after(0, lambda: self._toggle_done(errs, "PowerShell"))

        threading.Thread(target=work, daemon=True).start()

    def _toggle_wsl(self):
        on = bool(self.wsl_var.get())
        h = p = ""
        note = None
        if on:
            r = self._validate()
            if not r:
                self._wsl_sw.deselect()
                return
            h, p = r
            if h.lower() in LOOPBACK_HOSTS and get_wsl_network_mode() != "Mirrored":
                note = "NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。"
        self._lock_actions(True)

        def work():
            errs = []
            for fn in (lambda: set_wsl_proxy(h, p, on),
                       lambda: set_git_proxy(h, p, on, use_wsl=True)):
                ret = fn()
                if ret is not True:
                    errs.append(str(ret))
            self._after(0, lambda: self._toggle_done(errs, "WSL", note))

        threading.Thread(target=work, daemon=True).start()

    # ── Restart WSL ──
    def _restart(self):
        self._lock_actions(True, self._rst, "重启中…")

        def do():
            ret = restart_wsl()
            self._after(0, lambda: self._rst_done(ret))

        threading.Thread(target=do, daemon=True).start()

    def _rst_done(self, ret):
        self._lock_actions(False)
        # A WSL restart is the moment a changed networkingMode takes effect,
        # so drop the cached value before the refresh re-reads it.
        _invalidate_wsl_network_mode_cache()
        if ret is True:
            self._async_refresh()
        else:
            messagebox.showerror("错误", f"重启失败: {ret}")

    # ── Pin / Topmost ──
    def _sync_pin_btn(self):
        if self.pin_var.get():
            self._pin_btn.configure(fg_color=C["accent"], hover_color=C["accent_h"],
                                     text_color="#FFFFFF")
        else:
            self._pin_btn.configure(fg_color=C["input_bg"], hover_color=C["border"],
                                     text_color=C["text2"])

    def _toggle_pin(self):
        new = not self.pin_var.get()
        self.pin_var.set(new)
        self.root.attributes("-topmost", new)
        self._sync_pin_btn()
        save_config(self.host_var.get().strip(), self.port_var.get().strip(),
                    self.ps_var.get(), self.wsl_var.get(), new, self._mode)

    # ── Theme toggle ──
    def _theme_icon(self):
        # Show the mode you'll switch TO.
        return "☀️" if self._mode == "dark" else "🌙"

    def _toggle_theme(self):
        self._mode = "dark" if self._mode == "light" else "light"
        apply_theme(self._mode)
        save_config(self.host_var.get().strip(), self.port_var.get().strip(),
                    self.ps_var.get(), self.wsl_var.get(), self.pin_var.get(),
                    self._mode)
        # Freeze window repainting during the bulk color update so the user
        # sees an instant snap instead of 200+ configure() calls repainting one
        # by one over ~180ms. WM_SETREDRAW tells the window to stop processing
        # paint messages; we flush all configures, then thaw and refresh once.
        hwnd = self.root.winfo_id()
        user32.SendMessageW(hwnd, WM_SETREDRAW, 0, 0)
        try:
            self.root.configure(fg_color=C["bg"])
            for w in self.root.winfo_children():
                if getattr(w, "_is_content", False):
                    self._apply_colors(w)
            self._dot.configure(text_color=C["green"] if self._proxy_on else C["text2"])
            self._sync_pin_btn()
        finally:
            user32.SendMessageW(hwnd, WM_SETREDRAW, 1, 0)
            # Force a single repaint now that all colors are set.
            self.root.update_idletasks()

    def _apply_colors(self, widget):
        """Recursively restyle an existing widget tree to the active palette
        without destroying any widgets. customtkinter supports dynamic color
        updates via .configure()."""
        if isinstance(widget, ctk.CTkButton):
            # Use the stable _role attribute (set at build time) rather than
            # matching on text, because button text can change (e.g. "重启中…").
            role = getattr(widget, "_role", None)
            if role == "primary":
                widget.configure(fg_color=C["accent"], hover_color=C["accent_h"],
                                 text_color="#FFFFFF", border_color=C["border"])
            elif role == "danger":
                widget.configure(fg_color="transparent", border_color=C["red"],
                                 text_color=C["red"], hover_color=C["danger_hover"])
            elif role == "secondary":
                widget.configure(fg_color=C["secondary"], hover_color=C["secondary_h"],
                                 text_color="#FFFFFF", border_color=C["border"])
            else:
                # Icon buttons (theme / pin / refresh).
                widget.configure(fg_color=C["input_bg"], hover_color=C["border"],
                                 text_color=C["text2"], border_color=C["border"])
        elif isinstance(widget, ctk.CTkLabel):
            # An explicit _tint wins (secondary headers that are bold yet
            # styled with text2); otherwise infer: bold -> text, regular -> text2.
            tint = getattr(widget, "_tint", None)
            if tint == "text2":
                widget.configure(text_color=C["text2"])
            else:
                weight = widget.cget("font").cget("weight") if hasattr(widget.cget("font"), "cget") else None
                widget.configure(text_color=C["text"] if weight == "bold" else C["text2"])
        elif isinstance(widget, ctk.CTkFrame):
            fg = widget.cget("fg_color")
            if fg in ("transparent", ""):
                widget.configure(fg_color="transparent", border_color=C["border"])
            else:
                widget.configure(fg_color=C["card"], border_color=C["border"])
        elif isinstance(widget, ctk.CTkEntry):
            widget.configure(fg_color=C["input_bg"], border_color=C["border"],
                             text_color=C["text"])
        elif isinstance(widget, ctk.CTkCheckBox):
            widget.configure(fg_color=C["accent"], hover_color=C["accent_h"],
                             border_color=C["border"], text_color=C["text"])
        elif isinstance(widget, ctk.CTkScrollableFrame):
            widget.configure(fg_color="transparent", border_color=C["border"])
        for child in widget.winfo_children():
            self._apply_colors(child)

    # ── Live Speed Sampler ──
    _MAX_DELTA = 100 * 1024 * 1024  # 100 MB/s sanity cap per sample

    @staticmethod
    def _speed_title():
        """Card caption. The tracked interface is chosen asynchronously and can
        change after startup, so this is refreshed each tick, not baked in."""
        return f"实时网速 ({_primary_iface or '-'})"

    def _tick_speed(self):
        """Sample net counters on a background thread every 1s.

        psutil.net_io_counters(pernic=True) takes ~10ms, which blocks the UI
        thread if called directly in the main loop. By running the sampling on
        a daemon thread and marshalling only the pre-formatted strings back via
        root.after(0, ...), the main-thread cost drops to ~0.1ms per tick."""
        self._speed_thread_stop = threading.Event()

        def sampler():
            prev = self._speed_prev
            while not self._speed_thread_stop.is_set():
                cur = get_net_counters()
                up_str = dn_str = None
                title = self._speed_title()
                if cur is not None and prev is not None:
                    up = cur[0] - prev[0]
                    dn = cur[1] - prev[1]
                    # Guard against counter resets/overflows: a negative delta
                    # or an implausibly large jump (>100 MB in one second)
                    # means the underlying counter wrapped or the interface was
                    # re-enumerated; skip this sample rather than showing a
                    # bogus spike.
                    if 0 <= up <= self._MAX_DELTA:
                        up_str = format_speed(up)
                    if 0 <= dn <= self._MAX_DELTA:
                        dn_str = format_speed(dn)
                prev = cur if cur is not None else prev

                def apply(u, d, t):
                    # Use _after for its generation guard: if a rebuild has
                    # happened since scheduling, the widget references may be
                    # stale and this callback safely no-ops. _after also
                    # swallows RuntimeError if the mainloop has terminated.
                    def do():
                        if self._speed_hdr is not None:
                            if self._speed_hdr.cget("text") != t:
                                self._speed_hdr.configure(text=t)
                        if u is not None and self._speed_up is not None:
                            self._speed_up.set_value(u)
                        if d is not None and self._speed_dn is not None:
                            self._speed_dn.set_value(d)
                    self._after(0, do)

                apply(up_str, dn_str, title)
                self._speed_thread_stop.wait(1.0)

        threading.Thread(target=sampler, daemon=True).start()

    # ── System Tray ──
    def _on_close(self):
        """Hide to tray instead of quitting."""
        self.root.withdraw()
        self._start_tray()

    def _start_tray(self):
        if self._tray_icon is not None:
            return
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", self._tray_show),
                pystray.MenuItem("退出", self._tray_quit),
            )
            self._tray_icon = pystray.Icon(
                "proxy_manager", _make_tray_image(), "代理管理器", menu
            )
            self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
            self._tray_thread.start()
        except Exception:
            # If tray can't start (e.g. headless), just quit.
            self.root.destroy()

    def _stop_tray(self):
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
            self._tray_thread = None

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self._show_window)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._quit)

    def _show_window(self):
        self._stop_tray()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self):
        # Bump generation so any pending _after callbacks (e.g. from the speed
        # sampler thread) become no-ops instead of touching destroyed widgets.
        self._generation += 1
        if hasattr(self, "_speed_thread_stop"):
            self._speed_thread_stop.set()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        self.root.destroy()


# ─── Entry ────────────────────────────────────────────────────────────────────

# Win32 constants for ShowWindow / SetForegroundWindow.
_SW_RESTORE = 9
_SW_SHOW = 5


def _try_activate_window():
    """Best-effort: find an existing ProxyManager window by title and bring it
    to the foreground.  Works even if the running instance was built from old
    code that doesn't poll for wake.signal."""
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
        if not hwnd:
            return
        # If the window is minimized or hidden, restore it first.
        ctypes.windll.user32.ShowWindow(hwnd, _SW_RESTORE)
        # AllowSetForegroundWindow lets us steal focus from the tray.
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def main():
    import sys
    # Catch uncaught exceptions in non-UI threads. UI-thread callback failures
    # are handled separately by _safe_report_exception above; both paths write
    # to the same crash.log via _log_crash.
    sys.excepthook = _log_crash

    # ── Single-instance guard ──
    # Use a named mutex so only one ProxyManager process lives at a time.
    # If a second launch is attempted, it writes a wake.signal file and exits;
    # the running instance polls for that file and brings its window back.
    mutex_name = "Local\\ProxyManagerSingleInstance"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
    already_running = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

    if already_running:
        # Two-pronged wake-up:
        # 1) Write a signal file that new-code instances poll for.
        # 2) Use Win32 FindWindow + ShowWindow to directly revive old-code
        #    instances that don't have the polling loop.
        try:
            os.makedirs(os.path.dirname(_SIGNAL_FILE), exist_ok=True)
            with open(_SIGNAL_FILE, "w") as f:
                f.write("show")
        except OSError:
            pass
        _try_activate_window()
        ctypes.windll.kernel32.CloseHandle(mutex)
        return

    # Clean up any stale signal file from a previous second-launch attempt.
    try:
        os.remove(_SIGNAL_FILE)
    except OSError:
        pass

    root = ctk.CTk()

    app = None
    try:
        app = App(root)
    except Exception:
        _log_crash(*sys.exc_info())
        # Fall through — the window is already visible; user can see it.

    # Ensure the window is visible, restored, and on-screen.  Customtkinter /
    # PyInstaller can leave the window in an iconic or off-screen state;
    # deiconify + lift + focus_force is the reliable triple to bring it back.
    root.deiconify()
    root.state("normal")
    root.lift()
    root.focus_force()

    if app is not None:
        # Poll for the wake.signal so a second launch brings us back.
        def _check_wake():
            if os.path.exists(_SIGNAL_FILE):
                try:
                    os.remove(_SIGNAL_FILE)
                except OSError:
                    pass
                app._show_window()
            root.after(1500, _check_wake)

        root.after(1500, _check_wake)

    root.mainloop()

    # Release the mutex on exit.
    ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
