"""
ProxyManager - backend library.

All system-touching logic used by the web UI (app.py + ui/index.html):
Windows user-environment proxy (registry), WinINET system proxy, WSL
bashrc/zshrc/fish injection across distros, git global proxy on both ends,
network/speed telemetry, WSL restart, crash logging. No UI code lives here.

Front-end entry point: app.py (pywebview + WebView2).
"""

import collections
import os
import re
import socket
import subprocess
import uuid
import ctypes
import winreg
from typing import Optional
from urllib.parse import quote, unquote, urlparse

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

# ─── Crash logging ────────────────────────────────────────────────────────────
# A --windowed build has no console, so unhandled exceptions would vanish
# silently. Every crash path funnels through one writer that appends a
# timestamped traceback to %APPDATA%\ProxyManager\crash.log.
_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
_CRASH_LOG = os.path.join(_APPDATA, "ProxyManager", "crash.log")

# Single-instance wake signal: a second launch writes "show" and exits; the
# running instance polls for it and brings its window back.
_SIGNAL_FILE = os.path.join(_APPDATA, "ProxyManager", "wake.signal")
SIGNAL_FILE = _SIGNAL_FILE


def _log_crash(exc_type, exc_val, exc_tb):
    """Append a timestamped traceback to crash.log. Best-effort: never raises."""
    import traceback
    import datetime
    try:
        os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {exc_type.__name__}: {exc_val}\n")
            traceback.print_tb(exc_tb, file=f)
            f.write("\n")
    except Exception:
        pass


log_crash = _log_crash  # public alias — app.py / tray / window code should use this

_LOGGED_KEYS_MAX = 256
_logged_keys: "collections.OrderedDict" = collections.OrderedDict()  # LRU via move-to-end


def log_exception(prefix: str = "") -> None:
    """Append the current exception as a non-fatal WARN to crash.log.

    Call from `except Exception` handlers that would otherwise `pass` so
    field failures are diagnosable in a --windowed build. Never raises.
    """
    import traceback
    import datetime
    import sys
    try:
        exc_type, exc_val, exc_tb = sys.exc_info()
        if exc_type is None:
            return
        os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            label = f" {prefix}" if prefix else ""
            f.write(f"[{datetime.datetime.now()}] WARN{label}: "
                    f"{exc_type.__name__}: {exc_val}\n")
            traceback.print_tb(exc_tb, file=f)
            f.write("\n")
    except Exception:
        pass


def log_exception_once(key: str, prefix: str = "") -> None:
    """Like log_exception, but only the first hit per process for `key`.

    Use on hot paths (speed sampler, resize watchdog) so a repeating
    failure cannot grow crash.log without bound.  Bounded to
    ``_LOGGED_KEYS_MAX`` entries via an LRU eviction policy.
    """
    if key in _logged_keys:
        _logged_keys.move_to_end(key)
        return
    _logged_keys[key] = True
    if len(_logged_keys) > _LOGGED_KEYS_MAX:
        _logged_keys.popitem(last=False)
    log_exception(prefix or key)


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

HWND_BROADCAST   = 0xFFFF
WM_SETTINGCHANGE = 0x001A

# ─── Constants ────────────────────────────────────────────────────────────────
APP_TITLE  = "代理管理器"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "7897"


def _config_dir() -> str:
    """Persist config under %APPDATA%\\ProxyManager so it survives PyInstaller
    --onefile runs (where __file__ points to a transient _MEIxxxxx dir)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ProxyManager")


config_dir = _config_dir  # public alias

CONFIG_FILE = os.path.join(_config_dir(), "config.json")

WSL_PROXY_BEGIN = "# >>> proxy-manager begin >>>"
WSL_PROXY_END   = "# <<< proxy-manager end <<<"
ENV_REG_PATH    = r"Environment"
NO_PROXY_VALUE  = "localhost,127.0.0.1,::1"
LOOPBACK_HOSTS  = {"127.0.0.1", "localhost", "::1", "::"}

# Sentinel strings the UI maps through i18n. Keep stable; do not translate here.
UNSET = "未设置"
UNDETECTED = "未检测到"

PROXY_SCHEMES = ("http", "socks5", "socks5h")


def _is_valid_port(p) -> bool:
    """ASCII digits only — str.isdigit() also accepts superscripts etc. that
    int() then rejects. Accepts int as well as str (callers mix both)."""
    if isinstance(p, bool) or p is None:
        return False
    if isinstance(p, int):
        return 1 <= p <= 65535
    s = str(p).strip()
    return s.isascii() and s.isdigit() and 1 <= int(s) <= 65535


is_valid_port = _is_valid_port  # public alias


def is_valid_scheme(s) -> bool:
    return str(s or "").strip().lower() in PROXY_SCHEMES


def is_valid_userinfo(s) -> bool:
    """Username or password: empty is fine; letters, digits, '.', '_' and '-' only.

    Tight charset so JS encodeURIComponent and Python quote() produce the same
    bytes — a looser set (e.g. `!'()*`) diverged and made switches flip off.
    """
    if s is None:
        return True
    text = str(s)
    if not text:
        return True
    return text.isascii() and all(c.isalnum() or c in "._-" for c in text)


def build_proxy_url(host, port, scheme="http", user="", password="") -> str:
    """Canonical proxy URL. Userinfo is percent-encoded so bash/git/ini stay safe."""
    sch = str(scheme or "http").strip().lower()
    if sch not in PROXY_SCHEMES:
        sch = "http"
    auth = ""
    if user:
        auth = quote(str(user), safe="")
        if password:
            auth += ":" + quote(str(password), safe="")
        auth += "@"
    return f"{sch}://{auth}{host}:{port}"


def parse_proxy_url(url):
    """Parse a proxy URL into {scheme, host, port, user, password} or None.

    Accepts http / https / socks5 / socks5h / socks4. https is normalized to
    http (we only ever write http:// for HTTP proxies); socks4 -> socks5
    for the scheme we manage.
    """
    if not url or not isinstance(url, str):
        return None
    s = url.strip()
    if "://" not in s:
        return None
    try:
        parsed = urlparse(s)
    except Exception:
        return None
    sch = (parsed.scheme or "").lower()
    if sch == "https":
        sch = "http"
    elif sch == "socks4":
        sch = "socks5"
    if sch not in PROXY_SCHEMES:
        return None
    # urlparse(.hostname) lowercases DNS names; surgical disable compares
    # the rebuilt URL as an exact string against registry/ini, so case must
    # be preserved from the original netloc.
    host = parsed.hostname
    raw = parsed.netloc or ""
    if "@" in raw:
        raw = raw.rsplit("@", 1)[-1]
    if raw.startswith("["):
        end = raw.find("]")
        if end > 0:
            host = raw[1:end]
    elif raw:
        host_part, sep, _rest = raw.rpartition(":")
        if sep and host_part:
            host = host_part
    if not host:
        return None
    try:
        port = parsed.port
    except (ValueError, TypeError):
        return None
    if not is_valid_port(port):
        return None
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    return {
        "scheme": sch,
        "host": host,
        "port": str(port),
        "user": user,
        "password": password,
    }


def wininet_url(host, port, scheme="http") -> str:
    """URL that get_sys_proxy() returns for a host/port/scheme we wrote.

    WinINET has no userinfo and treats SOCKS as a single `socks=` token, so
    socks5h collapses to socks5:// and auth is dropped.
    """
    sch = str(scheme or "http").strip().lower()
    if sch.startswith("socks"):
        return f"socks5://{host}:{port}"
    return f"http://{host}:{port}"


def is_proxy_url(url) -> bool:
    return parse_proxy_url(url) is not None


def rewrite_proxy_host(url, new_host) -> str:
    """Keep scheme/userinfo/port, replace host (WSL NAT gateway swap)."""
    parsed = parse_proxy_url(url)
    if not parsed:
        return url
    return build_proxy_url(
        new_host, parsed["port"], parsed["scheme"],
        parsed["user"], parsed["password"])


def nat_host(host: str) -> str:
    """In WSL2 NAT mode, loopback inside the distro does not reach Windows;
    return the host gateway IP. Mirrored mode and non-loopback hosts pass through."""
    if str(host).lower() in LOOPBACK_HOSTS and get_wsl_network_mode() != "Mirrored":
        gw = _get_wsl_gateway_ip()
        if gw:
            return gw
    return host


def _proxy_kwargs(scheme="http", user="", password=""):
    return {"scheme": scheme or "http", "user": user or "", "password": password or ""}


# ─── Network Info ─────────────────────────────────────────────────────────────
def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        log_exception_once("local_ip", "get_local_ip")
        return UNDETECTED


def get_wsl_ip_and_block():
    """Fetch WSL IP, the proxy-manager shell block, and the WSL git proxy in
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
            f"sed -n '/{re.escape(WSL_PROXY_BEGIN)}/,/{re.escape(WSL_PROXY_END)}/p' ~/.bashrc ~/.zshrc 2>/dev/null; "
            "cat ~/.config/fish/conf.d/proxy-manager.fish 2>/dev/null;"
            f"echo '{sep2}';"
            "git config --global --get http.proxy 2>/dev/null;"
            # git --get exits 1 when the key is unset, so the raw exit code can't
            # distinguish "works" from "fails"; `true` normalizes it to 0 iff
            # bash actually ran. A non-zero code means wsl.exe itself failed
            # (feature disabled / no distro installed).
            "true"
        )
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
        if r.returncode != 0:
            return ("WSL 不可用", "", UNSET)
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
        git_part = git_part if git_part else UNSET
        return (ip, block_part, git_part)
    except Exception:
        log_exception("get_wsl_ip_and_block")
        return ("WSL 未运行", "", UNSET)


# Cache is keyed by .wslconfig's mtime so editing networkingMode and clicking
# refresh picks up the new value instead of returning a stale, never-expiring
# result (the original version cached indefinitely once first computed).
_wsl_network_mode_cache: Optional[tuple] = None  # (mtime, mode_str)


def _invalidate_wsl_network_mode_cache():
    """Drop the cached networkingMode so the next read re-parses .wslconfig.
    Called after a WSL restart, which is when a config change takes effect."""
    global _wsl_network_mode_cache
    _wsl_network_mode_cache = None


invalidate_wsl_network_mode_cache = _invalidate_wsl_network_mode_cache


def get_wsl_network_mode():
    global _wsl_network_mode_cache
    try:
        p = os.path.expanduser(r"~\.wslconfig")
        mtime = os.path.getmtime(p) if os.path.exists(p) else None
        cached = _wsl_network_mode_cache  # snapshot: another thread may clear it
        if cached is not None:
            cached_mtime, cached_mode = cached
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
                        raw = m.group(1).strip().strip('"').strip("'")
                        low = raw.lower()
                        if low == "mirrored":
                            mode = "Mirrored"
                        elif low == "nat":
                            mode = "NAT"
                        else:
                            mode = raw.capitalize()
                        _wsl_network_mode_cache = (mtime, mode)
                        return mode
        _wsl_network_mode_cache = (mtime, "默认(NAT)")
        return _wsl_network_mode_cache[1]
    except Exception:
        log_exception_once("wsl_mode", "get_wsl_network_mode")
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
    """Human-readable rate, e.g. 1.2 MB/s (SI, same scale as the Mbps setting)."""
    # 999.6 would round to "1000 B/s" — promote at the DISPLAY boundary.
    if bytes_per_sec < 999.5:
        return f"{bytes_per_sec:.0f} B/s"
    units = ["KB", "MB", "GB"]
    val = bytes_per_sec / 1000  # 先转为 KB
    idx = 0
    while val >= 999.95 and idx < len(units) - 1:
        val /= 1000
        idx += 1
    return f"{val:.1f} {units[idx]}/s"


# ─── Proxy Logic ──────────────────────────────────────────────────────────────
def get_ps_status():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0, winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, "HTTP_PROXY")
            return v
        except FileNotFoundError:
            return None
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None


def get_ps_no_proxy():
    """Raw user NO_PROXY env value ('' when unset)."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0, winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, "NO_PROXY")
            return str(v)
        except FileNotFoundError:
            return ""
        finally:
            winreg.CloseKey(key)
    except OSError:
        return ""


def get_ps_proxy_vars():
    """Read HTTP_PROXY, HTTPS_PROXY and ALL_PROXY from Windows user env."""
    result = {"HTTP_PROXY": UNSET, "HTTPS_PROXY": UNSET, "ALL_PROXY": UNSET}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0, winreg.KEY_READ)
        try:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                try:
                    v, _ = winreg.QueryValueEx(key, name)
                    result[name] = v
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
    except OSError:
        pass
    return result


def _read_wsl_block() -> str:
    """Return the text between the proxy-manager markers in ~/.bashrc,
    ~/.zshrc, and the fish conf.d file (empty if absent). Scoping reads to
    this block avoids mis-picking up commented or user-written export lines
    elsewhere in the file."""
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c",
             f"sed -n '/{re.escape(WSL_PROXY_BEGIN)}/,/{re.escape(WSL_PROXY_END)}/p' ~/.bashrc ~/.zshrc 2>/dev/null; "
             "cat ~/.config/fish/conf.d/proxy-manager.fish 2>/dev/null"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
        return r.stdout
    except Exception:
        return ""


def _parse_export(block: str, name: str):
    """Pull NAME from a POSIX `export NAME=` line or a fish `set -gx NAME`."""
    m = re.search(
        r'^export\s+' + re.escape(name) + r'\s*=\s*["\']?([^"\'\n]+)["\']?\s*$',
        block, re.MULTILINE,
    )
    if m:
        return m.group(1).strip()
    m = re.search(
        r'^set\s+-gx\s+' + re.escape(name) + r'\s+["\']?([^"\'\n]+)["\']?\s*$',
        block, re.MULTILINE,
    )
    return m.group(1).strip() if m else None


def get_wsl_proxy_vars(block: Optional[str] = None):
    """Read HTTP_PROXY / HTTPS_PROXY / ALL_PROXY produced by this tool from WSL.
    Pass an existing block (from _read_wsl_block) to avoid a redundant WSL
    subprocess call."""
    result = {"HTTP_PROXY": UNSET, "HTTPS_PROXY": UNSET, "ALL_PROXY": UNSET}
    if block is None:
        block = _read_wsl_block()
    if not block:
        return result
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
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


def _no_proxy_entries(value: str):
    return [e.strip() for e in (value or "").split(",") if e.strip()]


def _merged_no_proxy(existing: str) -> str:
    """User's existing NO_PROXY + our loopback entries (union, user's first)."""
    entries = _no_proxy_entries(existing)
    for e in NO_PROXY_VALUE.split(","):
        if e not in entries:
            entries.append(e)
    return ",".join(entries)


def _stripped_no_proxy(existing: str) -> str:
    """User's NO_PROXY minus exactly the entries this tool manages — anything
    the user added themselves survives a disable."""
    ours = set(NO_PROXY_VALUE.split(","))
    return ",".join(e for e in _no_proxy_entries(existing) if e not in ours)


def _broadcast_env_change():
    """Broadcast WM_SETTINGCHANGE so already-running processes pick up the
    user-env change (same message the system sends after env edits)."""
    ret = ctypes.c_long()
    user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
        0x0002, 5000, ctypes.byref(ret),
    )


def set_ps_proxy(host, port, enable=True, scheme="http", user="", password=""):
    url = build_proxy_url(host, port, scheme, user, password)
    env_names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_READ)
        try:
            if enable:
                for n in env_names:
                    winreg.SetValueEx(key, n, 0, winreg.REG_SZ, url)
                # Keep loopback traffic direct without clobbering a user-set
                # NO_PROXY: merge ours in on enable, strip only ours on disable.
                try:
                    prev, _ = winreg.QueryValueEx(key, "NO_PROXY")
                except FileNotFoundError:
                    prev = ""
                winreg.SetValueEx(key, "NO_PROXY", 0, winreg.REG_SZ,
                                  _merged_no_proxy(prev))
            else:
                # Surgical: only drop values equal to the URL this tool manages.
                # A proxy the user set by hand for another endpoint survives.
                # This is an intentional design choice — only exact matches are
                # removed so user-configured proxies for other endpoints are
                # never clobbered by a disable.
                removed = False
                for n in env_names:
                    try:
                        cur, _ = winreg.QueryValueEx(key, n)
                    except FileNotFoundError:
                        continue
                    if cur == url:
                        try:
                            winreg.DeleteValue(key, n)
                            removed = True
                        except FileNotFoundError:
                            pass
                if removed:
                    try:
                        prev, _ = winreg.QueryValueEx(key, "NO_PROXY")
                    except FileNotFoundError:
                        prev = None
                    if prev is not None:
                        kept = _stripped_no_proxy(prev)
                        if kept:
                            winreg.SetValueEx(key, "NO_PROXY", 0, winreg.REG_SZ, kept)
                        else:
                            try:
                                winreg.DeleteValue(key, "NO_PROXY")
                            except FileNotFoundError:
                                pass
        finally:
            winreg.CloseKey(key)
        # Broadcast env change so already-running processes pick it up.
        _broadcast_env_change()
        return True
    except OSError as e:
        return str(e)


def set_ps_env_vars(values):
    """Write user env vars from {name: value}, deleting keys whose value is
    empty/None. Used to put back the exact pre-enable HTTP_PROXY /
    HTTPS_PROXY / ALL_PROXY / NO_PROXY state, which a single URL cannot
    express when the user's variables differed.  Only the four proxy
    variables are accepted — this function is reachable from the webview
    bridge, so an unrestricted key name would let it write arbitrary
    HKCU\\Environment values."""
    allowed = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return True
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0,
                             winreg.KEY_SET_VALUE)
        try:
            for n, v in values.items():
                if v:
                    winreg.SetValueEx(key, n, 0, winreg.REG_SZ, str(v))
                else:
                    try:
                        winreg.DeleteValue(key, n)
                    except FileNotFoundError:
                        pass
        finally:
            winreg.CloseKey(key)
        _broadcast_env_change()
        return True
    except OSError as e:
        return str(e)


INET_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_PROXY_OVERRIDE_OURS = {"localhost", "127.0.0.1", "<local>"}


def _wininet_notify():
    """Ask WinINET to re-read Internet Settings so browsers pick the change up."""
    try:
        wininet = ctypes.WinDLL("wininet")
        wininet.InternetSetOptionW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        wininet.InternetSetOptionW.restype = ctypes.c_int
        wininet.InternetSetOptionW(None, 39, None, 0)  # SETTINGS_CHANGED
        wininet.InternetSetOptionW(None, 37, None, 0)  # REFRESH
    except Exception:
        pass


def _sys_proxy_server_to_url(server: str):
    s = (server or "").strip()
    if not s:
        return None
    if "=" in s:
        picked = None
        socks_picked = None
        for part in s.split(";"):
            pl = part.lower()
            if pl.startswith("socks=") or pl.startswith("socks5="):
                socks_picked = part.split("=", 1)[1]
            elif pl.startswith("http=") and not pl.startswith("https="):
                picked = part.split("=", 1)[1]
                break
            elif pl.startswith("https=") and picked is None:
                picked = part.split("=", 1)[1]
        if picked:
            s = picked.strip()
        elif socks_picked:
            hostport = socks_picked.strip()
            if hostport.startswith("socks"):
                return hostport
            return f"socks5://{hostport}"
        else:
            s = s.split("=", 1)[-1].strip()
    if s.startswith("http://") or s.startswith("https://") \
            or s.startswith("socks5://") or s.startswith("socks5h://") \
            or s.startswith("socks4://"):
        return s
    return f"http://{s}"


def get_sys_proxy():
    """WinINET user proxy ('未设置' when disabled / empty)."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_READ)
        try:
            try:
                enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except FileNotFoundError:
                enable = 0
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except FileNotFoundError:
                server = ""
        finally:
            winreg.CloseKey(key)
        if not enable:
            return UNSET
        return _sys_proxy_server_to_url(str(server)) or UNSET
    except OSError:
        return UNDETECTED


def set_sys_proxy(host, port, enable=True, scheme="http", user="", password=""):
    """Write/clear Windows system proxy (Internet Options / WinINET).
    Disable is surgical: only turns it off when the current server is ours.

    WinINET does not honor SOCKS userinfo; SOCKS schemes write `socks=host:port`
    (no auth). HTTP schemes write `host:port` as before.
    """
    sch = str(scheme or "http").strip().lower()
    if sch.startswith("socks"):
        server = f"socks={host}:{port}"
    else:
        server = f"{host}:{port}"
    url = build_proxy_url(host, port, scheme, user, password)
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_READ)
        try:
            if enable:
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
                try:
                    prev, _ = winreg.QueryValueEx(key, "ProxyOverride")
                except FileNotFoundError:
                    prev = ""
                entries = [e.strip() for e in str(prev).split(";") if e.strip()]
                for e in ("localhost", "127.0.0.1", "<local>"):
                    if e not in entries:
                        entries.append(e)
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ,
                                  ";".join(entries))
            else:
                try:
                    cur, _ = winreg.QueryValueEx(key, "ProxyServer")
                except FileNotFoundError:
                    cur = ""
                cur_url = _sys_proxy_server_to_url(str(cur)) if cur else None
                if cur_url == url or str(cur).strip() == server:
                    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
                    try:
                        winreg.DeleteValue(key, "ProxyServer")
                    except FileNotFoundError:
                        pass
                    # Clean up ProxyOverride entries we appended on enable,
                    # preserving any the user added themselves.
                    try:
                        prev, _ = winreg.QueryValueEx(key, "ProxyOverride")
                    except FileNotFoundError:
                        prev = None
                    if prev is not None:
                        kept = [e.strip() for e in str(prev).split(";")
                                if e.strip() and e.strip() not in _PROXY_OVERRIDE_OURS]
                        if kept:
                            winreg.SetValueEx(key, "ProxyOverride", 0,
                                              winreg.REG_SZ, ";".join(kept))
                        else:
                            try:
                                winreg.DeleteValue(key, "ProxyOverride")
                            except FileNotFoundError:
                                pass
        finally:
            winreg.CloseKey(key)
        _wininet_notify()
        return True
    except OSError as e:
        return str(e)


def get_sys_proxy_override():
    """Raw WinINET ProxyOverride (bypass list) value ('' when unset)."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, "ProxyOverride")
            return str(v)
        except FileNotFoundError:
            return ""
        finally:
            winreg.CloseKey(key)
    except OSError:
        return ""


def set_sys_proxy_override(override):
    """Write ProxyOverride verbatim ('' deletes the value). Used to put back
    the exact pre-enable bypass list instead of subtracting our entries —
    subtraction would also drop entries the user had configured themselves
    before this tool first ran."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_SET_VALUE)
        try:
            if override:
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ,
                                  str(override))
            else:
                try:
                    winreg.DeleteValue(key, "ProxyOverride")
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        _wininet_notify()
        return True
    except OSError as e:
        return str(e)


def wsl_available():
    """True iff `wsl -e` can actually run bash (feature enabled + a distro
    installed). wsl.exe itself always exists in System32 on Win10/11, so its
    mere presence proves nothing."""
    try:
        r = subprocess.run(["wsl", "-e", "echo", "ok"],
                           capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def _get_wsl_gateway_ip():
    """Return the WSL default gateway (= the Windows host as seen from WSL2
    NAT mode), or None if it cannot be determined (WSL down, WSL1, etc.)."""
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c",
             "ip route show default 2>/dev/null | awk '/default/ {print $3; exit}'"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
        ip = r.stdout.strip()
        if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", ip):
            return ip
    except Exception:
        log_exception_once("wsl_gateway", "get_wsl_gateway_ip")
    return None


get_wsl_gateway_ip = _get_wsl_gateway_ip  # public alias


def proxy_port_alive(host, port, timeout=1.5):
    """TCP connect probe: catches 'proxy app not running / wrong port' before
    enabling, which otherwise silently breaks all terminal networking."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def wsl_default_shell():
    """Basename of the default distro user's login shell, or None if WSL is
    unusable. bash/zsh/fish/sh are written to; anything else is warned."""
    try:
        r = subprocess.run(["wsl", "-e", "bash", "-c", 'basename "${SHELL:-bash}"'],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None
    except Exception:
        return None


def _wsl_distros():
    """Names of installed user distros (wsl -l -q emits UTF-16LE). Docker's
    infra distros are skipped. Empty list -> WSL unusable or unparseable;
    callers then fall back to the default distro."""
    try:
        r = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            return []
        names = (r.stdout.decode("utf-16-le", errors="ignore")
                 .replace("\x00", "").lstrip("\ufeff").split())
        return [n for n in names if n and not n.startswith("docker-desktop")]
    except Exception:
        return []


_wsl_apply_all = True  # True: every user distro; False: default distro only


def set_wsl_apply_all(enabled: bool) -> None:
    global _wsl_apply_all
    _wsl_apply_all = bool(enabled)


def _wsl_targets():
    """Distro names to read/write. Empty string = WSL's default distro."""
    if _wsl_apply_all:
        return _wsl_distros() or [""]
    return [""]


def _wsl_write_failure(r, distro):
    """Classify a failed `wsl -e bash` run without echoing wsl's own console
    text (redirected output may be UTF-16 and decodes as mojibake)."""
    if not wsl_available():
        return "未检测到可用的 WSL 发行版"
    detail = (r.stderr or "").strip().splitlines()
    detail = detail[0].strip() if detail else ""
    if detail and detail.isascii():
        return f"写入 WSL 代理配置失败：{detail}"
    return "写入 WSL 代理配置失败（权限不足或磁盘已满？）"


def set_wsl_proxy(host, port, enable=True, scheme="http", user="", password=""):
    write_host = nat_host(host) if enable else host
    url = build_proxy_url(write_host, port, scheme, user, password)
    # Merge loopback bypass entries into whatever NO_PROXY the user already
    # exported earlier in bashrc / the environment, instead of overwriting.
    block = f"""{WSL_PROXY_BEGIN}
export HTTP_PROXY="{url}"
export HTTPS_PROXY="{url}"
export ALL_PROXY="{url}"
export http_proxy="{url}"
export https_proxy="{url}"
export all_proxy="{url}"
_pm_np="${{NO_PROXY:-}}"
for _pm_e in localhost 127.0.0.1 ::1; do
  case ",${{_pm_np}}," in
    *",${{_pm_e}},"*) ;;
    *) _pm_np="${{_pm_np:+${{_pm_np}},}}${{_pm_e}}" ;;
  esac
done
export NO_PROXY="$_pm_np"
export no_proxy="$NO_PROXY"
unset _pm_np _pm_e
{WSL_PROXY_END}"""
    try:
        # One bash call per distro: strip the managed block from bashrc AND
        # zshrc (same POSIX exports), then (on enable) append a fresh copy.
        # Writes THROUGH the file (not sed -i) so stow/chezmoi symlinks stay
        # links. The POSIX block arrives on stdin (no shell interpolation).
        # fish gets its own conf.d file (different syntax).
        begin, end = re.escape(WSL_PROXY_BEGIN), re.escape(WSL_PROXY_END)
        create = '[ -f "$f" ] || : > "$f"; ' if enable else '[ -f "$f" ] || return; '
        append = 'cat "$block" >> "$f"; ' if enable else ''
        script = (
            ('block=$(mktemp); cat > "$block"; ' if enable else '')
            + 'apply_posix() { f="$1"; ' + create
            + '[ -n "$(tail -c1 "$f" 2>/dev/null)" ] && printf \'\\n\' >> "$f"; '
            + f'tmp=$(mktemp) && sed \'/{begin}/,/{end}/d\' "$f" > "$tmp" '
            + '&& cat "$tmp" > "$f"; rm -f "$tmp"; '
            + append
            + '}; '
            + 'apply_posix "$HOME/.bashrc"; apply_posix "$HOME/.zshrc"; '
        )
        if enable:
            import base64 as _b64
            fish_text = (
                f"{WSL_PROXY_BEGIN}\n"
                f'set -gx HTTP_PROXY "{url}"\n'
                f'set -gx HTTPS_PROXY "{url}"\n'
                f'set -gx ALL_PROXY "{url}"\n'
                f'set -gx http_proxy "{url}"\n'
                f'set -gx https_proxy "{url}"\n'
                f'set -gx all_proxy "{url}"\n'
                "if not set -q NO_PROXY\n"
                "    set -gx NO_PROXY\n"
                "end\n"
                "for _pm_e in localhost 127.0.0.1 ::1\n"
                "    if not contains -- $_pm_e (string split -- , \"$NO_PROXY\")\n"
                '        if test -n "$NO_PROXY"\n'
                '            set -gx NO_PROXY "$NO_PROXY,$_pm_e"\n'
                "        else\n"
                '            set -gx NO_PROXY "$_pm_e"\n'
                "        end\n"
                "    end\n"
                "end\n"
                'set -gx no_proxy "$NO_PROXY"\n'
                "set -e _pm_e\n"
                f"{WSL_PROXY_END}\n"
            )
            b64 = _b64.b64encode(fish_text.encode("utf-8")).decode("ascii")
            script += (
                'mkdir -p "$HOME/.config/fish/conf.d"; '
                f'echo {b64} | (base64 -d 2>/dev/null || base64 --decode) '
                '> "$HOME/.config/fish/conf.d/proxy-manager.fish"; '
                'rm -f "$block"'
            )
        else:
            script += 'rm -f "$HOME/.config/fish/conf.d/proxy-manager.fish"'

        # Apply to every user distro (multi-distro setups), skipping Docker's
        # infra distros. Enable fails loudly; disable is best-effort.
        errs = []
        for d in (_wsl_targets()):
            label = f"WSL({d})" if d else "WSL"
            try:
                cmd = ["wsl"] + (["-d", d] if d else []) + ["-e", "bash", "-c", script]
                r = subprocess.run(
                    cmd,
                    input=(block + "\n").encode("utf-8") if enable else None,
                    capture_output=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if enable and r.returncode != 0:
                    errs.append(f"{label}: {_wsl_write_failure(r, d)}")
            except Exception as e:
                if enable:
                    errs.append(f"{label}: {e}")
        if enable and errs:
            return "; ".join(errs)
        return True
    except Exception as e:
        # Disabling is best-effort cleanup: a missing/broken WSL has nothing
        # to clean, and reporting it would block the whole disable flow.
        if not enable:
            return True
        return str(e) or "WSL 操作失败"


def _git_win_unset(url: str) -> None:
    """Remove one exact git proxy URL on Windows. --fixed-value needs Git
    2.33+; fall back to a regex unset so older Git still disables."""
    for key in ("http.proxy", "https.proxy"):
        r = subprocess.run(
            ["git", "config", "--global", "--fixed-value", "--unset-all", key, url],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode in (0, 5):
            continue
        subprocess.run(
            ["git", "config", "--global", "--unset-all", key, re.escape(url)],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)


def _bash_sq(s: str) -> str:
    """Escape a string for use inside single quotes in bash.
    ``'`` becomes ``'\\''`` (end-quote, escaped-quote, start-quote)."""
    return s.replace("'", "'\\''")


def _git_wsl_unset_cmd(url: str) -> str:
    """Bash snippet: surgical unset with --fixed-value, regex fallback.

    The regex fallback embeds ``re.escape(url)`` in single quotes — re.escape
    does NOT escape single quotes (``re.escape("a'b") == "a'b"``), so a URL
    containing ``'`` could break out of the quoting.  Shell-escape the
    regex-escaped string with ``_bash_sq`` to close that hole."""
    esc = _bash_sq(re.escape(url))
    sq = _bash_sq(url)
    return (
        f"(git config --global --fixed-value --unset-all http.proxy '{sq}' "
        f"|| git config --global --unset-all http.proxy '{esc}') 2>/dev/null; "
        f"(git config --global --fixed-value --unset-all https.proxy '{sq}' "
        f"|| git config --global --unset-all https.proxy '{esc}') 2>/dev/null; true"
    )


def set_git_proxy(host, port, enable=True, use_wsl=False,
                  scheme="http", user="", password=""):
    """Configure git's global http.proxy/https.proxy, either on Windows
    (use_wsl=False) or inside WSL (use_wsl=True).

    Host/port/userinfo are pre-validated by the UI, so the URL is injection-safe.
    Disabling is best-effort: a missing git binary (or missing WSL) simply
    means there is nothing to clean up and is reported as success, so it
    never blocks the unconditional disable flow."""
    url = build_proxy_url(host, port, scheme, user, password)
    try:
        if use_wsl:
            errs = []
            for d in (_wsl_targets()):
                label = f"WSL({d})" if d else "WSL"
                try:
                    if enable:
                        sq = _bash_sq(url)
                        cmd = (f"git config --global --replace-all http.proxy '{sq}' && "
                               f"git config --global --replace-all https.proxy '{sq}'")
                    elif host or port:
                        # Remove only values equal to the URL this tool manages;
                        # any proxy value the user set themselves survives.
                        cmd = _git_wsl_unset_cmd(url)
                    else:
                        # Address/port fields were cleared: the managed URL is
                        # unknown, fall back to clearing the keys entirely.
                        cmd = ("git config --global --unset-all http.proxy 2>/dev/null; "
                               "git config --global --unset-all https.proxy 2>/dev/null; true")
                    r = subprocess.run(
                        ["wsl"] + (["-d", d] if d else []) + ["-e", "bash", "-c", cmd],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    if enable and r.returncode != 0:
                        # Avoid echoing wsl's own messages: redirected console
                        # output may be UTF-16 and would decode as mojibake.
                        if not wsl_available():
                            errs.append(f"{label}: 未检测到可用的 WSL 发行版")
                        elif r.returncode == 127:
                            errs.append(f"{label}: 未找到 git")
                        else:
                            errs.append(f"{label}: git 配置失败（exit {r.returncode}）")
                except Exception as e:
                    if enable:
                        errs.append(f"{label}: {e}")
            if enable and errs:
                return "; ".join(errs)
        else:
            if enable:
                for key in ("http.proxy", "https.proxy"):
                    r = subprocess.run(["git", "config", "--global", "--replace-all", key, url],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    if r.returncode != 0:
                        return (r.stderr or f"exit {r.returncode}").strip() or f"exit {r.returncode}"
            else:
                if host or port:
                    # Surgical: remove only the value this tool set, so a
                    # proxy the user configured by hand for another endpoint
                    # survives a disable.
                    _git_win_unset(url)
                else:
                    # Address/port fields were cleared: managed URL unknown.
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
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        v = r.stdout.strip()
        return v if v else UNSET
    except Exception:
        log_exception_once("git_win", "get_git_proxy_win")
        return UNDETECTED


# ─── npm / pip endpoints ──────────────────────────────────────────────────────
# Both tools are configured by editing their user-level INI-style config file
# directly (npm: ~/.npmrc, pip: %APPDATA%\pip\pip.ini) instead of shelling out
# to `npm config` / `pip config`: no PATH dependency, no multi-second CLI
# startup, and every byte we don't own (comments, registry lines, other keys)
# survives untouched. Same disable contract as the git endpoints: only values
# equal to this tool's managed URL are removed, so a proxy the user set by
# hand for another endpoint is never clobbered.

NPM_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".npmrc")
NPM_PROXY_KEYS = ("proxy", "https-proxy")


def _pip_config_path():
    """pip's Windows user config (%APPDATA%\\pip\\pip.ini). The legacy
    %USERPROFILE%\\pip\\pip.ini is left alone — pip itself still reads it,
    but the APPDATA path is what modern pip writes and what users expect."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "pip", "pip.ini")


def _read_ini(path):
    """Read an INI-ish file as (text, encoding). Delegates to
    ``_decode_ini_bytes`` so the decoding logic is not duplicated."""
    with open(path, "rb") as f:
        raw = f.read()
    return _decode_ini_bytes(raw)


def _ini_get(text, key, section=None):
    """Value of `key` in INI text, or None when absent. Quotes stripped.
    Without `section` the whole file is searched (npmrc is flat); with one,
    only lines inside that section count (pip reads proxy under [global] —
    an [install] proxy must not make the UI report enabled)."""
    if section is None:
        m = re.search(rf"(?im)^[ \t]*{re.escape(key)}[ \t]*[:=][ \t]*(.*?)[ \t]*$",
                      text)
        if not m:
            return None
        return m.group(1).strip().strip('"').strip("'") or None
    cur = None
    for line in text.splitlines():
        m = re.match(r"^[ \t]*\[([^\]]*)\][ \t]*$", line)
        if m:
            cur = m.group(1).strip().lower()
            continue
        if cur != section.lower():
            continue
        m = re.match(rf"[ \t]*{re.escape(key)}[ \t]*[:=][ \t]*(.*?)[ \t]*$",
                     line, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip('"').strip("'") or None
    return None


def _ini_edit(text, keys, url, equals, section=None, eol="\n"):
    """Rewrite `keys` lines in INI text: set them to `url`, or (url=None)
    remove each line whose value equals `equals` — surgical removal, so a
    user-set proxy for a different endpoint survives a disable. With a
    `section`, only lines INSIDE it are touched (pip reads proxy under
    [global]; an [install] proxy belongs to another tool and must survive
    verbatim). Keys absent from the section are appended right after its
    header (created when missing), else at the end (npm's .npmrc is flat).
    Every other line, key style (`:` vs `=`), quoting and line ending
    is preserved; appended lines use `eol` (the file's own dominant ending).
    Returns the new text."""
    line_re = re.compile(
        r"^([ \t]*)(" + "|".join(re.escape(k) for k in keys)
        + r")([ \t]*[:=][ \t]*)(.*?)([ \t]*)$", re.IGNORECASE)
    hdr_re = re.compile(r"^[ \t]*\[([^\]]*)\][ \t]*$",
                        re.IGNORECASE) if section else None
    out, seen, sec_at = [], set(), None
    in_sec = section is None  # flat files (npm): every line is in scope
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if hdr_re:
            hdr = hdr_re.match(body)
            if hdr:
                in_sec = hdr.group(1).strip().lower() == section.lower()
                if in_sec:
                    sec_at = len(out)
                out.append(line)
                continue
        if not in_sec:
            out.append(line)
            continue
        m = line_re.match(body)
        if not m:
            out.append(line)
            continue
        seen.add(m.group(2).lower())
        cur = m.group(4).strip().strip('"').strip("'")
        if url is None:
            if cur and cur != equals:
                out.append(line)  # user's own value: untouched
                continue
            continue              # our line: dropped entirely
        q = m.group(4)[:1] if m.group(4)[:1] in "\"'" else ""
        out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{q}{url}{q}"
                   f"{line[len(body):]}")
    missing = [k for k in keys if k not in seen]
    if url is not None and missing:
        add = "".join(f"{k} = {url}{eol}" for k in missing)
        if sec_at is not None:
            if not out[sec_at].endswith(("\n", "\r")):
                out[sec_at] += eol
            out.insert(sec_at + 1, add)
        else:
            if out and not out[-1].endswith(("\n", "\r")):
                out[-1] += eol
            if section:
                out.append(f"{eol if out else ''}[{section}]{eol}{add}")
            else:
                out.append(add)
    return "".join(out)


def _eol_of(text):
    """Dominant line ending of an INI text (CRLF wins when present)."""
    return "\r\n" if "\r\n" in text else "\n"


def _decode_ini_bytes(data: bytes):
    """Decode INI file bytes as (text, encoding). Tries UTF-8 (BOM optional)
    then the ANSI codepage — a .npmrc / pip.conf hand-saved with GBK comments
    must not make the WSL reads/writes raise UnicodeDecodeError (which is not
    an OSError, so it slipped past every endpoint's error handling). The
    returned encoding is reused on write so the file round-trips."""
    try:
        enc = "utf-8-sig" if data[:3] == b"\xef\xbb\xbf" else "utf-8"
        return data.decode("utf-8-sig"), enc
    except UnicodeDecodeError:
        return data.decode("mbcs", errors="replace"), "mbcs"


def get_npm_proxy():
    """npm's user-level proxy (UNSET when no config/no key, UNDETECTED when
    the file exists but can't be read)."""
    try:
        text, _enc = _read_ini(NPM_CONFIG_PATH)
    except FileNotFoundError:
        return UNSET
    except OSError:
        return UNDETECTED
    return _ini_get(text, "proxy") or UNSET


def set_npm_proxy(host, port, enable=True, scheme="http", user="", password=""):
    """Write/clear npm's proxy + https-proxy in ~/.npmrc. npm re-reads this
    file on every invocation, so the next npm command picks the change up.
    Disable is best-effort: a missing file means nothing to clean up."""
    url = build_proxy_url(host, port, scheme, user, password)
    try:
        try:
            text, _enc = _read_ini(NPM_CONFIG_PATH)
        except FileNotFoundError:
            if not enable:
                return True
            text = ""
        new = _ini_edit(text, NPM_PROXY_KEYS, url if enable else None,
                        equals=url, eol=_eol_of(text))
        if new != text:
            os.makedirs(os.path.dirname(NPM_CONFIG_PATH), exist_ok=True)
            # newline="" keeps \r\n verbatim (text mode would translate \n
            # to \r\n and double the \r on CRLF files). Always plain UTF-8:
            # it's the only encoding npm reads.
            with open(NPM_CONFIG_PATH, "w", encoding="utf-8", newline="") as f:
                f.write(new)
        return True
    except OSError as e:
        return True if not enable else str(e)


def get_pip_proxy():
    """pip's user-config proxy ('未设置' / '未检测到', same semantics as npm).
    Only [global] counts: pip ignores an [install] proxy, so reporting it
    would show enabled while pip stays direct."""
    try:
        text, _enc = _read_ini(_pip_config_path())
    except FileNotFoundError:
        return UNSET
    except OSError:
        return UNDETECTED
    return _ini_get(text, "proxy", section="global") or UNSET


def set_pip_proxy(host, port, enable=True, scheme="http", user="", password=""):
    """Write/clear the proxy key pip reads: `proxy` under [global] (--proxy is
    a general option in pip, so global.proxy applies to every pip command,
    not just `pip install`). File-based, so it applies to every interpreter's
    pip including `py -m pip` without pip.exe on PATH. Disable is
    best-effort."""
    url = build_proxy_url(host, port, scheme, user, password)
    path = _pip_config_path()
    try:
        try:
            text, enc = _read_ini(path)
        except FileNotFoundError:
            if not enable:
                return True
            text, enc = "", "utf-8"
        new = _ini_edit(text, ("proxy",), url if enable else None,
                        equals=url, section="global", eol=_eol_of(text))
        if new != text:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding=enc, newline="", errors="replace") as f:
                f.write(new)
        return True
    except OSError as e:
        return True if not enable else str(e)


# ─── npm / pip inside WSL ─────────────────────────────────────────────────────
# Same contract as every other endpoint, but the config files live inside each
# distro. Reads pull the file back over wsl.exe stdout and edits happen on the
# Windows side with the same _ini_edit used for the Windows files; writes push
# the bytes back over stdin (never through shell interpolation — same channel
# the WSL shell block uses). Linux config paths differ from Windows:
#   npm: ~/.npmrc (same name)
#   pip: ~/.config/pip/pip.conf (XDG); the legacy ~/.pip/pip.conf is also
#        cleaned surgically on disable, but only the XDG path is written.

_NPMrc_PATH_WSL = "~/.npmrc"
_PIP_CONF_PATH_WSL = "~/.config/pip/pip.conf"
_PIP_CONF_LEGACY_WSL = "~/.pip/pip.conf"


def _bash_path(path):
    """Make a `~/...` path usable inside a double-quoted bash string: tilde
    expansion does NOT happen inside quotes, but $HOME does. Absolute paths
    pass through unchanged."""
    return path.replace("~", "$HOME", 1) if path.startswith("~") else path


def _wsl_pull_file(distro, path):
    """Read a file from a distro, or None when it doesn't exist. Output is
    raw bytes so binary-ish content (GBK comments, BOM) round-trips."""
    shown = path  # keep the user-facing ~ form for error messages
    path = _bash_path(path)
    cmd = (f'if [ -f "{path}" ]; then cat "{path}"; else exit 3; fi')
    r = subprocess.run(
        ["wsl"] + (["-d", distro] if distro else []) + ["-e", "bash", "-c", cmd],
        capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode == 3:
        return None
    if r.returncode != 0:
        raise OSError(f"读取 {shown} 失败（exit {r.returncode}）")
    return r.stdout


def _wsl_push_file(distro, path, data: bytes):
    """Atomically replace a file in a distro with `data` (arrives on stdin)."""
    shown = path
    path = _bash_path(path)
    # mkdir first: a failure here must not strand the mktemp file mid-pipeline.
    script = (f'mkdir -p "$(dirname "{path}")" && tmp=$(mktemp) && cat > "$tmp" && '
              f'cat "$tmp" > "{path}" && rm -f "$tmp"')
    r = subprocess.run(
        ["wsl"] + (["-d", distro] if distro else []) + ["-e", "bash", "-c", script],
        input=data, capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise OSError(f"写入 {shown} 失败（exit {r.returncode}）")


def _wsl_edit_ini(distro, path, keys, url, equals, section=None):
    """Pull → _ini_edit → push one config file in one distro. Returns False
    (no error) when the file doesn't exist on a disable; True when changed or
    identical; raises OSError on wsl failures."""
    data = _wsl_pull_file(distro, path)
    if data is None:
        if url is None:
            return False
        text, enc, eol = "", "utf-8", "\n"
    else:
        text, enc = _decode_ini_bytes(data)
        eol = _eol_of(text)
    new = _ini_edit(text, keys, url, equals=equals, section=section, eol=eol)
    if new == text:
        return True
    _wsl_push_file(distro, path, new.encode(enc))
    return True


def get_npm_proxy_wsl(distro=None):
    """npm proxy inside the distro(s). Reads every user distro and returns
    the first proxy found (multi-distro setups are rare and usually kept in
    sync; the badge shows one URL). '未设置' / '未检测到' as elsewhere."""
    try:
        for d in (_wsl_targets()):
            try:
                data = _wsl_pull_file(d, _NPMrc_PATH_WSL)
            except OSError:
                return UNDETECTED
            if data is None:
                continue
            v = _ini_get(_decode_ini_bytes(data)[0], "proxy")
            if v:
                return v
        return UNSET
    except Exception:
        log_exception("get_npm_proxy_wsl")
        return UNDETECTED


def get_pip_proxy_wsl(distro=None):
    """pip proxy inside the distro(s): XDG pip.conf first, then the legacy
    ~/.pip/pip.conf (both are read by pip itself, in that order)."""
    try:
        for d in (_wsl_targets()):
            for path in (_PIP_CONF_PATH_WSL, _PIP_CONF_LEGACY_WSL):
                try:
                    data = _wsl_pull_file(d, path)
                except OSError:
                    return UNDETECTED
                if data is None:
                    continue
                v = _ini_get(_decode_ini_bytes(data)[0], "proxy")
                if v:
                    return v
        return UNSET
    except Exception:
        log_exception("get_pip_proxy_wsl")
        return UNDETECTED


def set_npm_proxy_wsl(host, port, enable=True, scheme="http", user="", password=""):
    """Write/clear npm's proxy + https-proxy in every user distro's ~/.npmrc.
    Same disable contract as the Windows npm endpoint. NAT mode swaps a
    loopback host for the Windows gateway IP, same as the WSL shell block."""
    write_host = nat_host(host) if enable else host
    url = build_proxy_url(write_host, port, scheme, user, password)
    try:
        errs = []
        for d in (_wsl_targets()):
            label = f"WSL({d})" if d else "WSL"
            try:
                _wsl_edit_ini(d, _NPMrc_PATH_WSL, NPM_PROXY_KEYS,
                              url if enable else None, equals=url)
            except OSError as e:
                if enable:
                    errs.append(f"{label}: {e}")
        if enable and errs:
            return "; ".join(errs)
        return True
    except Exception:
        log_exception("set_npm_proxy_wsl")
        return True if not enable else "WSL 操作失败"


def set_pip_proxy_wsl(host, port, enable=True, scheme="http", user="", password=""):
    """Write/clear `proxy` under [global] in every user distro's XDG
    pip.conf; disable also cleans the legacy ~/.pip/pip.conf. NAT mode swaps
    a loopback host for the Windows gateway IP."""
    write_host = nat_host(host) if enable else host
    url = build_proxy_url(write_host, port, scheme, user, password)
    try:
        errs = []
        for d in (_wsl_targets()):
            label = f"WSL({d})" if d else "WSL"
            try:
                _wsl_edit_ini(d, _PIP_CONF_PATH_WSL, ("proxy",),
                              url if enable else None, equals=url,
                              section="global")
                if not enable:
                    # Legacy path: surgical clean only, never written.
                    _wsl_edit_ini(d, _PIP_CONF_LEGACY_WSL, ("proxy",),
                                  None, equals=url)
            except OSError as e:
                if enable:
                    errs.append(f"{label}: {e}")
        if enable and errs:
            return "; ".join(errs)
        return True
    except Exception:
        log_exception("set_pip_proxy_wsl")
        return True if not enable else "WSL 操作失败"


def restart_wsl():
    try:
        subprocess.run(["wsl", "--shutdown"],
                       capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        # A cold start after --shutdown can easily exceed 15s on slow disks
        # (vhdx compaction, AV scans) — a timeout here would misreport the
        # restart as failed, so probe with retries.
        last_err = None
        for _ in range(3):
            try:
                r = subprocess.run(["wsl", "-e", "echo", "ok"],
                                   capture_output=True, timeout=25,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    return True
                last_err = f"exit {r.returncode}"
            except subprocess.TimeoutExpired:
                last_err = "探活超时"
        return f"未检测到可用的 WSL 发行版（{last_err}）"
    except Exception as e:
        return str(e)
