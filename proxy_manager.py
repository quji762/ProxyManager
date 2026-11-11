"""
ProxyManager - backend library.

All system-touching logic used by the web UI (app.py + ui/index.html):
Windows user-environment proxy (registry), WinINET system proxy, WSL
bashrc/zshrc/fish injection across distros, git global proxy on both ends,
network/speed telemetry, WSL restart, crash logging. No UI code lives here.

Front-end entry point: app.py (pywebview + WebView2).
"""

import collections
import ctypes
import os
import re
import socket
import subprocess
import uuid
import winreg
from urllib.parse import quote, unquote, urlparse

from messages import LJoin, LStr

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
SIGNAL_FILE = os.path.join(_APPDATA, "ProxyManager", "wake.signal")


def _log_crash(exc_type, exc_val, exc_tb):
    """Append a timestamped traceback to crash.log. Best-effort: never raises."""
    import datetime
    import traceback
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
    import datetime
    import sys
    import traceback
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

WSL_PROXY_BEGIN = "# >>> proxy-manager begin >>>"
WSL_PROXY_END   = "# <<< proxy-manager end <<<"
ENV_REG_PATH    = r"Environment"
NO_PROXY_VALUE  = "localhost,127.0.0.1,::1"
LOOPBACK_HOSTS  = {"127.0.0.1", "localhost", "::1", "::"}

# Windows' own UI (System Properties / Environment Variables) only shows the
# UPPERCASE proxy variables, while curl and most *nix tooling read the
# lowercase spellings. Every enable writes BOTH cases for each name so
# neither family is left pointing at a stale proxy, and every read prefers
# the lowercase spelling with the uppercase one as fallback.
PS_ENV_URL_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                    "http_proxy", "https_proxy", "all_proxy")
PS_ENV_NO_PROXY_NAMES = ("NO_PROXY", "no_proxy")
PS_ENV_NAMES = PS_ENV_URL_NAMES + PS_ENV_NO_PROXY_NAMES

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

    Tight charset so JS encodeURIComponent and Python quote(safe="!") produce
    the same bytes. `@` and `!` are allowed (common proxy passwords).
    """
    if s is None:
        return True
    text = str(s)
    if not text:
        return True
    return text.isascii() and all(c.isalnum() or c in "._-@!" for c in text)


def build_proxy_url(host, port, scheme="http", user="", password="") -> str:
    """Canonical proxy URL. Userinfo is percent-encoded so bash/git/ini stay safe."""
    sch = str(scheme or "http").strip().lower()
    if sch not in PROXY_SCHEMES:
        sch = "http"
    auth = ""
    if user:
        auth = quote(str(user), safe="!")
        if password:
            auth += ":" + quote(str(password), safe="!")
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


def wininet_server(host, port, scheme="http") -> str:
    """ProxyServer literal that set_sys_proxy() writes for this address.

    Windows leaves the last ProxyServer in the registry when the user turns
    the proxy off in the Internet Options UI (ProxyEnable flips to 0, the
    server string stays), so a residual server is not necessarily a foreign
    one: compare against this before treating it as user state.
    """
    sch = str(scheme or "http").strip().lower()
    if sch.startswith("socks"):
        return f"socks={host}:{port}"
    return f"{host}:{port}"


def is_proxy_url(url) -> bool:
    return parse_proxy_url(url) is not None


# ─── Network Info ─────────────────────────────────────────────────────────────
def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        log_exception_once("local_ip", "get_local_ip")
        return UNDETECTED


def _wsl_ip_and_block_one(distro=""):
    """One distro: (ip, block, git) or None if wsl.exe itself failed."""
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
        "git config --global --get http.proxy 2>/dev/null "
        "|| git config --global --get https.proxy 2>/dev/null;"
        # git --get exits 1 when the key is unset, so the raw exit code can't
        # distinguish "works" from "fails"; `true` normalizes it to 0 iff
        # bash actually ran. A non-zero code means wsl.exe itself failed
        # (feature disabled / no distro installed).
        "true"
    )
    try:
        r = subprocess.run(
            ["wsl"] + (["-d", distro] if distro else []) + ["-e", "bash", "-c", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        # 单个发行版损坏/首次启动卡死不应让整个 WSL 状态被判"未运行"，
        # 更不能阻断后续发行版的读取。
        return None
    if r.returncode != 0:
        return None
    out = r.stdout or ""
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
    ip = ip_part.strip() or "未获取到"
    git_part = git_part if git_part else UNSET
    return (ip, block_part, git_part)


def get_wsl_ip_and_block():
    """Fetch WSL IP, the proxy-manager shell block, and the WSL git proxy.

    Walks the same distro list as writes (`_wsl_targets`): the first IP,
    managed block, and git proxy found win. Each distro is one wsl.exe
    call (the three reads stay merged to keep cold-start cost down).
    Returns (ip_str, block_str, git_proxy_str)."""
    try:
        ip_found, block_found, git_found = "", "", UNSET
        any_ok = False
        for d in _wsl_targets():
            got = _wsl_ip_and_block_one(d)
            if got is None:
                continue
            any_ok = True
            ip, block, git = got
            if ip and ip != "未获取到" and not ip_found:
                ip_found = ip
            if block and not block_found:
                block_found = block
            if git and git != UNSET and git_found == UNSET:
                git_found = git
            if ip_found and block_found and git_found != UNSET:
                break
        if not any_ok:
            return ("WSL 不可用", "", UNSET)
        return (ip_found or "未获取到", block_found, git_found)
    except Exception:
        log_exception("get_wsl_ip_and_block")
        return ("WSL 未运行", "", UNSET)


# Cache is keyed by .wslconfig's mtime so editing networkingMode and clicking
# refresh picks up the new value instead of returning a stale, never-expiring
# result (the original version cached indefinitely once first computed).
_wsl_network_mode_cache: tuple | None = None  # (mtime, mode_str)


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
            with open(p, encoding="utf-8") as f:
                lines = f.read().splitlines()
            # .wslconfig is INI-shaped: only honor networkingMode under [wsl2].
            in_wsl2 = False
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith(("#", ";")):
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


def _pick_primary_iface() -> str | None:
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
    units = ["KB", "MB", "GB", "TB"]
    val = bytes_per_sec / 1000  # 先转为 KB
    idx = 0
    while val >= 999.95 and idx < len(units) - 1:
        val /= 1000
        idx += 1
    return f"{val:.1f} {units[idx]}/s"


# ─── Proxy Logic ──────────────────────────────────────────────────────────────
# Rollback snapshots store (value, reg_type) per name; a name that did not
# exist before we wrote it is snapshotted as this sentinel so restoring it
# deletes the value instead of writing None back.
_REG_MISSING = object()


def _restore_reg_value(key, name, snap) -> None:
    """Put one registry value back to its snapshot: write the original
    (value, type) pair, or delete the value when it did not exist before.
    The type is kept verbatim — ProxyEnable must come back as REG_DWORD,
    not as the REG_SZ a plain str() write would leave behind."""
    if snap is _REG_MISSING:
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass
        return
    value, reg_type = snap
    winreg.SetValueEx(key, name, 0, reg_type, value)


def _reg_snapshot(key, names):
    """{name: (value, type) or _REG_MISSING} for `names` under an open key."""
    snap = {}
    for n in names:
        try:
            snap[n] = winreg.QueryValueEx(key, n)
        except FileNotFoundError:
            snap[n] = _REG_MISSING
    return snap


def _ps_env_first(names):
    """First HKCU\\Environment value among `names`, or None when none is set.
    Lowercase spellings come first: curl and most *nix tooling read those,
    while Windows' own UI writes the uppercase ones."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0,
                             winreg.KEY_READ)
        try:
            for n in names:
                try:
                    v, _ = winreg.QueryValueEx(key, n)
                    return v
                except FileNotFoundError:
                    continue
            return None
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None


def get_ps_status():
    """User-env HTTP proxy URL — either spelling counts as 'enabled', so a
    machine where only http_proxy is set no longer reads as '未设置'."""
    return _ps_env_first(("http_proxy", "HTTP_PROXY"))


def get_ps_no_proxy():
    """Raw user NO_PROXY env value ('' when unset); either spelling."""
    return str(_ps_env_first(("no_proxy", "NO_PROXY")) or "")


def get_ps_proxy_vars():
    """Read HTTP_PROXY, HTTPS_PROXY and ALL_PROXY from Windows user env.
    Each protocol variable prefers the lowercase spelling (curl's) and
    falls back to the uppercase one (Windows UI's)."""
    result = {"HTTP_PROXY": UNSET, "HTTPS_PROXY": UNSET, "ALL_PROXY": UNSET}
    for name in result:
        v = _ps_env_first((name.lower(), name))
        if v is not None:
            result[name] = v
    return result


def get_ps_env_snapshot():
    """{name: value or None} for every PS_ENV_NAMES name, both spellings.

    Where get_ps_proxy_vars collapses each protocol variable to one
    canonical key (lowercase preferred), an enable writes all eight names
    — the three URL variables in BOTH cases plus both NO_PROXY spellings —
    so a caller that has to put the pre-enable state back needs one entry
    per name: a three-key view cannot tell "the user only ever set the
    lowercase name" from "both were set", and the names it omits are
    exactly the ones a restore would otherwise delete or overwrite.
    Missing and empty values read as None (unset); the key is opened once
    and every name is read from that one handle.
    """
    result = dict.fromkeys(PS_ENV_NAMES)
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0,
                             winreg.KEY_READ)
        try:
            for n in PS_ENV_NAMES:
                try:
                    v, _ = winreg.QueryValueEx(key, n)
                except FileNotFoundError:
                    continue
                if v is not None and v != "":
                    result[n] = v
        finally:
            winreg.CloseKey(key)
    except OSError:
        # Unreadable key: every name reads as unset, same as _ps_env_first.
        pass
    return result


def _read_wsl_block() -> str:
    """Return the text between the proxy-manager markers in ~/.bashrc,
    ~/.zshrc, and the fish conf.d file (empty if absent). Scoping reads to
    this block avoids mis-picking up commented or user-written export lines
    elsewhere in the file. Walks `_wsl_targets` so a block on a non-default
    distro is still visible when writes go to every distro."""
    try:
        cmd = _wsl_block_read_cmd()
        for d in _wsl_targets():
            try:
                r = subprocess.run(
                    ["wsl"] + (["-d", d] if d else []) + ["-e", "bash", "-c", cmd],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            except subprocess.TimeoutExpired:
                continue
            text = (r.stdout or "").strip()
            if text:
                return r.stdout
        return ""
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


def get_wsl_proxy_vars(block: str | None = None):
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


def get_wsl_status(block: str | None = None):
    if block is None:
        block = _read_wsl_block()
    if not block:
        return None
    url = _parse_export(block, "HTTP_PROXY")
    return url if url else "configured"


def _no_proxy_entries(value: str):
    """NO_PROXY accepts both ',' and ';' as separators (curl uses ',',
    some corporate setups ';'); split on either so a mixed list still
    round-trips through the merge/strip paths."""
    return [e.strip() for e in re.split(r"[,;]", value or "") if e.strip()]


def _merged_no_proxy(existing: str) -> str:
    """User's existing NO_PROXY + our loopback entries (union, user's first).

    Dedup compares case-insensitively (hosts are case-insensitive), but the
    user's original spelling is always the one kept in the output."""
    entries = _no_proxy_entries(existing)
    seen = {e.lower() for e in entries}
    for e in NO_PROXY_VALUE.split(","):
        if e.lower() not in seen:
            entries.append(e)
            seen.add(e.lower())
    return ",".join(entries)


def _stripped_no_proxy(existing: str) -> str:
    """User's NO_PROXY minus exactly the entries this tool manages — anything
    the user added themselves survives a disable. Matching is
    case-insensitive (hosts are case-insensitive)."""
    ours = {e.lower() for e in NO_PROXY_VALUE.split(",")}
    return ",".join(e for e in _no_proxy_entries(existing)
                    if e.lower() not in ours)


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
    # NO_PROXY is merged (never overwritten with the URL), so the URL loop
    # only covers the protocol variables — in both spellings.
    url_names = PS_ENV_URL_NAMES
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_READ)
        try:
            if enable:
                # Snapshot every original value first so a mid-loop failure
                # puts each key back exactly as it was (deleting the ones
                # that did not exist) — a blind delete would drop a proxy
                # the user had configured by hand.
                original = _reg_snapshot(key, PS_ENV_NAMES)
                written = []
                try:
                    for n in url_names:
                        winreg.SetValueEx(key, n, 0, winreg.REG_SZ, url)
                        written.append(n)
                    # Keep loopback traffic direct without clobbering a user-set
                    # NO_PROXY: merge ours in on enable, strip only ours on disable.
                    no_proxy = str(_ps_env_first(("no_proxy", "NO_PROXY")) or "")
                    merged = _merged_no_proxy(no_proxy)
                    for n in PS_ENV_NO_PROXY_NAMES:
                        winreg.SetValueEx(key, n, 0, winreg.REG_SZ, merged)
                        written.append(n)
                except OSError:
                    for n in written:
                        try:
                            _restore_reg_value(key, n, original[n])
                        except OSError:
                            # One key failing to roll back must not stop the
                            # others; the outer handler reports the failure.
                            pass
                    raise
            else:
                # Surgical: only drop values equal to the URL this tool manages.
                # A proxy the user set by hand for another endpoint survives.
                # This is an intentional design choice — only exact matches are
                # removed so user-configured proxies for other endpoints are
                # never clobbered by a disable.
                removed = False
                for n in url_names:
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
                    for n in PS_ENV_NO_PROXY_NAMES:
                        try:
                            prev, _ = winreg.QueryValueEx(key, n)
                        except FileNotFoundError:
                            continue
                        kept = _stripped_no_proxy(prev)
                        if kept:
                            winreg.SetValueEx(key, n, 0, winreg.REG_SZ, kept)
                        else:
                            try:
                                winreg.DeleteValue(key, n)
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
    """Put back the exact pre-enable state of the proxy env vars from
    {name: value}, deleting keys whose value is empty/None. Used because a
    single URL cannot express a state where HTTP_PROXY / HTTPS_PROXY /
    ALL_PROXY / NO_PROXY differed. Both spellings of the four proxy
    variables are accepted — an enable writes all eight — and any canonical
    name the caller does NOT mention is deleted too: an enable may have
    written a key the stash never captured (a stash taken by an older
    version only knew the four uppercase names), and leaving it behind
    would keep a proxy URL alive after the "restore". Any other key name is
    rejected: this function is reachable from the webview bridge, so an
    unrestricted key name would let it write arbitrary HKCU\\Environment
    values."""
    allowed = set(PS_ENV_NAMES)
    values = {k: v for k, v in values.items() if k in allowed}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_REG_PATH, 0,
                             winreg.KEY_SET_VALUE)
        try:
            for n in PS_ENV_NAMES:
                v = values.get(n)
                if v:
                    winreg.SetValueEx(key, n, 0, winreg.REG_SZ, str(v))
                else:
                    # Not in the stash (or stashed as empty): the key did not
                    # exist before the enable, so it must not exist after the
                    # restore either.
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
# The order enable appends them in. Disable must strip exactly the tail
# this append produced, so a '<local>' the user already had (a Windows
# default!) is never mistaken for one of ours.
_PROXY_OVERRIDE_OURS_ORDER = ("localhost", "127.0.0.1", "<local>")


def _strip_our_proxy_override(value):
    """Undo exactly what enable appended to ProxyOverride, or None when the
    current value does not end with such a tail.

    Subtraction (dropping every entry named like ours) would also delete
    entries the user configured themselves — '<local>' ships as a Windows
    default. Instead the longest suffix whose removal round-trips through
    the enable append is stripped; a value the user edited afterwards (or
    one that keeps their own copies of our names) is returned as None so
    the caller leaves it untouched."""
    entries = [e.strip() for e in str(value or "").split(";") if e.strip()]
    for k in range(len(entries), 0, -1):
        kept = entries[:len(entries) - k]
        if kept + [e for e in _PROXY_OVERRIDE_OURS_ORDER if e not in kept] \
                == entries:
            return kept
    return None


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
            if pl.startswith(("socks=", "socks5=")):
                socks_picked = part.split("=", 1)[1]
            elif pl.startswith("http="):
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
    if s.startswith(("http://", "https://", "socks5://", "socks5h://", "socks4://")):
        return s
    return f"http://{s}"


def sys_proxy_per_machine() -> bool:
    """企业策略 ProxySettingsPerUser=0 时 WinINET 使用 HKLM 机器级代理，
    写 HKCU 的用户级设置虽然读写一致，浏览器却不会走——开关看似生效
    实则无效。检测到时调用方应给出警示。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as k:
            value, _ = winreg.QueryValueEx(k, "ProxySettingsPerUser")
            return int(value) == 0
    except (OSError, ValueError):
        return False


def get_sys_proxy():
    """WinINET user proxy ('未设置' when disabled / empty).

    Callers wanting PAC (AutoConfigURL) awareness use get_sys_proxy_ex().
    """
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
        # Some tools write ProxyEnable as REG_SZ ("0"/"1") — a non-empty
        # string is truthy in Python, so "0" used to read as enabled.
        # Same coercion as sys_proxy_per_machine.
        try:
            enabled = int(enable) != 0
        except (TypeError, ValueError):
            enabled = False
        if not enabled:
            return UNSET
        return _sys_proxy_server_to_url(str(server)) or UNSET
    except OSError:
        return UNDETECTED


def get_sys_proxy_server_raw() -> str:
    """Raw WinINET ProxyServer regardless of ProxyEnable ('' when unset).

    Windows keeps the previous ProxyServer in the registry after the user
    turns the proxy off in the Internet Options UI: ProxyEnable flips to 0
    but the server string stays. get_sys_proxy() reports that state as
    UNSET, so the disable path used to delete a value the user never asked
    this tool to remove — this reader exposes it so it can be stashed and
    written back verbatim.
    """
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, "ProxyServer")
            return str(v)
        except FileNotFoundError:
            return ""
        finally:
            winreg.CloseKey(key)
    except OSError:
        return ""


def _read_autoconfig_url():
    """Raw WinINET AutoConfigURL (PAC script), '' when unset."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(key, "AutoConfigURL")
            return str(v)
        except FileNotFoundError:
            return ""
        finally:
            winreg.CloseKey(key)
    except OSError:
        return ""


def get_sys_proxy_ex():
    """get_sys_proxy plus PAC awareness: {url, autoconfig_url, pac}.

    url:    same value as get_sys_proxy() (WinINET static proxy).
    pac:    True while an AutoConfigURL is registered — browsers then use the
            PAC script and may ignore the static ProxyServer entirely.
    autoconfig_url: raw PAC URL ('' when unset).
    """
    url = get_sys_proxy()
    autoconfig_url = _read_autoconfig_url()
    return {
        "url": url,
        "autoconfig_url": autoconfig_url,
        "pac": bool(autoconfig_url),
    }


def set_sys_proxy(host, port, enable=True, scheme="http", user="", password=""):
    """Write/clear Windows system proxy (Internet Options / WinINET).
    Disable is surgical: only turns it off when the current server is ours.

    WinINET does not honor SOCKS userinfo; SOCKS schemes write `socks=host:port`
    (no auth). HTTP schemes write `host:port` as before.

    Returns True on success, an error string on failure. When a PAC
    (AutoConfigURL) is registered, returns (True, note) — the static proxy is
    still written, but browsers may keep routing through the PAC script.
    """
    sch = str(scheme or "http").strip().lower()
    server = f"socks={host}:{port}" if sch.startswith("socks") else f"{host}:{port}"
    url = build_proxy_url(host, port, scheme, user, password)
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_READ)
        try:
            if enable:
                # Server first, then the enable flag: a failed ProxyServer
                # write must not leave ProxyEnable=1 pointing at a stale/empty
                # value (which breaks every browser on the machine). Every
                # value is snapshotted up front so a failure at ANY step
                # restores the pre-enable state instead of leaving a
                # half-configured proxy behind.
                original = _reg_snapshot(
                    key, ("ProxyServer", "ProxyEnable", "ProxyOverride"))
                try:
                    winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
                    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                    try:
                        prev, _ = winreg.QueryValueEx(key, "ProxyOverride")
                    except FileNotFoundError:
                        prev = ""
                    entries = [e.strip() for e in str(prev).split(";") if e.strip()]
                    for e in _PROXY_OVERRIDE_OURS_ORDER:
                        if e not in entries:
                            entries.append(e)
                    winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ,
                                      ";".join(entries))
                except OSError:
                    for n, snap in original.items():
                        try:
                            _restore_reg_value(key, n, snap)
                        except OSError:
                            pass
                    raise
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
                    # Clean up the ProxyOverride tail we appended on enable,
                    # preserving any entries the user added themselves — a
                    # value that no longer ends with our tail is left alone.
                    try:
                        prev, _ = winreg.QueryValueEx(key, "ProxyOverride")
                    except FileNotFoundError:
                        prev = None
                    if prev is not None:
                        kept = _strip_our_proxy_override(prev)
                        if kept is None:
                            pass          # not our tail: untouched
                        elif kept:
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
        if enable:
            pac = _read_autoconfig_url()
            if pac:
                return (True, LStr("sysPacNote", {"url": pac}))
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


def set_sys_proxy_server(server):
    """Write ProxyServer verbatim ('' deletes the value), never touching
    ProxyEnable.

    Puts back a residual server the disable path deleted while the proxy was
    off — the user's "off but the address still remembered" state. Re-arming
    the proxy is NOT this function's job, so ProxyEnable is left exactly as
    it is. Returns True on success, an error string on failure.
    """
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_REG_PATH, 0,
                             winreg.KEY_SET_VALUE)
        try:
            if server:
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ,
                                  str(server))
            else:
                try:
                    winreg.DeleteValue(key, "ProxyServer")
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


_wsl_distros_cache = None  # (time.monotonic(), [names]) — TTL cache, see below
_WSL_DISTRO_TTL = 2.0


def _invalidate_wsl_distros_cache():
    """Drop the cached `wsl -l -q` enumeration so the next read re-runs it.
    Called after a WSL restart, which can change the installed distros."""
    global _wsl_distros_cache
    _wsl_distros_cache = None


invalidate_wsl_distros_cache = _invalidate_wsl_distros_cache


def _wsl_distros():
    """Names of installed user distros (wsl -l -q emits UTF-16LE). Docker's
    infra distros are skipped. Empty list -> WSL unusable or unparseable;
    callers then fall back to the default distro.

    A single port switch walks this list ~10 times (one per status read and
    per WSL-side setter target), each a separate wsl.exe cold start, so the
    result is cached for _WSL_DISTRO_TTL seconds. Only a NON-EMPTY list is
    cached: a failure, a timeout or an empty enumeration must not freeze
    "WSL is unusable" for the next caller (WSL may come back a second
    later). Callers get a copy and must not mutate the cached list."""
    import time
    global _wsl_distros_cache
    cached = _wsl_distros_cache  # snapshot: another thread may clear it
    if cached is not None:
        t, names = cached
        if time.monotonic() - t < _WSL_DISTRO_TTL:
            return list(names)
    try:
        r = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            return []
        raw = (r.stdout.decode("utf-16-le", errors="ignore")
               .replace("\x00", "").lstrip("\ufeff"))
        names = [n.strip() for n in raw.replace("\r\n", "\n").split("\n") if n.strip()]
        names = [n for n in names if n and not n.startswith("docker-desktop")]
        if names:
            _wsl_distros_cache = (time.monotonic(), list(names))
        return names
    except Exception:
        return []


_wsl_apply_all = True  # True: every user distro; False: default distro only


def set_wsl_apply_all(enabled: bool) -> None:
    global _wsl_apply_all
    _wsl_apply_all = bool(enabled)


def get_wsl_apply_all() -> bool:
    """Current scope: True = every user distro, False = default distro only."""
    return _wsl_apply_all


def wsl_default_distro_name() -> str:
    """Name of WSL's default distro, or "" when it cannot be determined
    (no WSL, wsl.exe failure, timeout, empty output).

    Callers MUST treat "" as "unknown" and skip any scope cleanup: guessing
    a default distro here would remove this tool's writes from a distro the
    user still works in (AGENTS.md: disable removes only our values)."""
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-c", 'printf %s "$WSL_DISTRO_NAME"'],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except Exception:
        return ""


def wsl_out_of_scope_distros() -> list:
    """User distros that leave the managed scope when it narrows to the
    default distro: every enumerated distro except the default one. Empty
    when the default distro is unknown or nothing can be enumerated."""
    default = wsl_default_distro_name()
    if not default:
        return []
    return [n for n in (_wsl_distros() or []) if n.casefold() != default.casefold()]


def wsl_sweep_targets():
    """Distros a quit-clear sweep must still cover while the scope is
    narrowed: this tool may have written them back when it was still
    "all user distros", and the narrowed scope hides them from every later
    read/write path. None when scope=all — _wsl_targets() already covers
    every distro there, so no sweep is needed."""
    if _wsl_apply_all:
        return None
    return _wsl_distros() or [""]


def _wsl_block_read_cmd() -> str:
    """Bash that prints the proxy-manager managed block (empty if absent).
    Shared by _read_wsl_block and wsl_managed_block_distros so both read
    exactly the same region."""
    return (
        f"sed -n '/{re.escape(WSL_PROXY_BEGIN)}/,/{re.escape(WSL_PROXY_END)}/p' "
        "~/.bashrc ~/.zshrc 2>/dev/null; "
        "cat ~/.config/fish/conf.d/proxy-manager.fish 2>/dev/null"
    )


def wsl_managed_block_distros(distros) -> list:
    """Subset of `distros` that still carries a proxy-manager shell block.

    Read-back verification: set_wsl_proxy's disable path is best-effort and
    reports success even when the rc write failed, so without reading the
    block back a failed cleanup would be reported as done. A distro that
    cannot be read (timeout / wsl failure) is skipped rather than fatal."""
    out = []
    for d in (distros or []):
        try:
            r = subprocess.run(
                ["wsl"] + (["-d", d] if d else []) + ["-e", "bash", "-c",
                                                     _wsl_block_read_cmd()],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except Exception:
            continue
        if (r.stdout or "").strip():
            out.append(d)
    return out


def wsl_recorded_url_residue(distro, git_url=None, npm_url=None, pip_url=None):
    """Which recorded URLs this tool wrote are STILL present in one distro's
    git/npm/pip config: a list of endpoint kinds ("gitwsl"/"npmwsl"/"pipwsl"),
    or None when the distro cannot be read.

    Read-back for the scope-cleanup path: the git/npm/pip disable setters are
    best-effort and return True even when a distro write failed, so the
    cleanup's error list cannot see a per-distro failure. Without reading the
    recorded URL back, a failed cleanup would clear the written-URL record
    and orphan the residue in a distro the narrowed scope no longer reads.
    None (unreadable) means "cleanup unverified", never "clean"."""
    out = []
    try:
        if git_url:
            for key in ("http.proxy", "https.proxy"):
                v = _wsl_git_config_get(distro, key)
                if v and v.strip() == str(git_url).strip():
                    out.append("gitwsl")
                    break
        if npm_url:
            data = _wsl_pull_file(distro, _NPMrc_PATH_WSL)
            if data is not None:
                text = _decode_ini_bytes(data)[0]
                if any((_ini_get(text, k) or "").strip()
                       == str(npm_url).strip() for k in NPM_PROXY_KEYS):
                    out.append("npmwsl")
        if pip_url:
            for path, section in ((_PIP_CONF_PATH_WSL, "global"),
                                  (_PIP_CONF_LEGACY_WSL, None)):
                data = _wsl_pull_file(distro, path)
                if data is None:
                    continue
                v = _ini_get(_decode_ini_bytes(data)[0], "proxy",
                             section=section)
                if v and v.strip() == str(pip_url).strip():
                    out.append("pipwsl")
                    break
        return out
    except Exception:
        return None


def _wsl_targets():
    """Distro names to read/write. Empty string = WSL's default distro."""
    if _wsl_apply_all:
        return _wsl_distros() or [""]
    return [""]


def _wsl_write_failure(r, distro):
    """Classify a failed `wsl -e bash` run without echoing wsl's own console
    text (redirected output may be UTF-16 and decodes as mojibake)."""
    where = f"WSL({distro}): " if distro else ""
    if not wsl_available():
        return LStr("errNoWslWhere", {"where": where})
    stderr = r.stderr or b""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    detail = stderr.strip().splitlines()
    detail = detail[0].strip() if detail else ""
    if detail and detail.isascii():
        return LStr("errWslWriteDetail", {"where": where, "detail": detail})
    return LStr("errWslWriteHint", {"where": where})


def _wsl_posix_script(enable: bool) -> str:
    """Bash function that strips the managed block from one shell rc file and
    (on enable) appends a fresh copy of it.

    The strip is an awk pass that removes only COMPLETE marker pairs: an end
    marker closes the most recent still-open begin marker (innermost first),
    and an unterminated block is buffered and re-emitted verbatim at EOF. So
    a stray begin marker can never delete the rest of the file (the old
    ``sed '/begin/,/end/d'`` deleted from begin to EOF whenever the end
    marker was missing) and no line is ever lost. The append is the last
    step of the ``&&`` chain — a failed backup/strip used to short-circuit
    straight past it and append a SECOND managed block on top of the old
    one. Any failure returns non-zero so the caller can roll the distro
    back instead of believing a half-written file."""
    create = '[ -f "$f" ] || : > "$f"; ' if enable else '[ -f "$f" ] || return 0; '
    then_body = 'cat "$block" >> "$f"; rc=$?; ' if enable else 'rc=0; '
    awk_prog = (
        '{ line = $0; sub(/\\r$/, "", line) } '
        'line == b { depth++; pend[depth] = $0 "\\n"; next } '
        'line == e && depth > 0 { delete pend[depth]; depth--; next } '
        'depth > 0 { pend[depth] = pend[depth] $0 "\\n"; next } '
        '{ print } '
        'END { for (i = 1; i <= depth; i++) printf "%s", pend[i] }'
    )
    return (
        'apply_posix() { f="$1"; ' + create
        + '[ -n "$(tail -c1 "$f" 2>/dev/null)" ] && printf \'\\n\' >> "$f"; '
        + 'tmp=$(mktemp) && bak=$(mktemp) && cp -a "$f" "$bak" 2>/dev/null '
        + '|| { rm -f "$tmp" "$bak"; return 1; }; '
        + "if awk -v b='" + WSL_PROXY_BEGIN + "' -v e='" + WSL_PROXY_END + "' '"
        + awk_prog + "' \"$f\" > \"$tmp\" "
        + '&& { cat "$tmp" > "$f" || cat "$bak" > "$f"; }; then '
        + then_body
        + 'else rc=1; fi; rm -f "$tmp" "$bak"; return $rc; }; '
    )


def set_wsl_proxy(host, port, enable=True, scheme="http", user="", password="",
                  distros=None):
    # NAT rewrite is the caller's job (js_api resolves via _wsl_url_target
    # ONCE, then writes and records the same host). Doing nat_host here would
    # be a second independent gateway probe and could disagree with the
    # "last written URL" record.
    # `distros` is an explicit target list for callers that must reach
    # distros the current scope no longer covers (scope-narrowing cleanup);
    # None keeps the scope-derived _wsl_targets() behavior byte for byte.
    write_host = host
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
        # fish gets its own conf.d file (different syntax). Per-file failures
        # set `fail`, so a half-written distro is reported instead of
        # silently "succeeding" (and is then rolled back, see below).
        # The block itself arrives on stdin (enable only); the strip function
        # is shared, so `rollback` is simply the disable program replayed on
        # the distros that already took the write.
        prefix = 'block=$(mktemp); cat > "$block"; ' if enable else ''
        fish_off = 'rm -f "$HOME/.config/fish/conf.d/proxy-manager.fish"'
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
            fish_on = (
                'mkdir -p "$HOME/.config/fish/conf.d" '
                f"&& printf '%s\\n' '{b64}' | (base64 -d 2>/dev/null || base64 --decode) "
                '> "$HOME/.config/fish/conf.d/proxy-manager.fish" '
                '&& rm -f "$block"'
            )
        else:
            fish_on = fish_off
        script = (
            'fail=0; ' + prefix + _wsl_posix_script(enable)
            + 'apply_posix "$HOME/.bashrc" || fail=1; '
            + 'apply_posix "$HOME/.zshrc" || fail=1; '
            + fish_on + ' || fail=1; '
            + 'exit "$fail"'
        )
        rollback = (
            'fail=0; ' + _wsl_posix_script(False)
            + 'apply_posix "$HOME/.bashrc" || fail=1; '
            + 'apply_posix "$HOME/.zshrc" || fail=1; '
            + fish_off + ' || fail=1; '
            + 'exit "$fail"'
        )

        # Apply to every user distro (multi-distro setups), skipping Docker's
        # infra distros. Enable fails loudly; disable is best-effort.
        errs = []
        done = []
        for d in (list(distros) if distros is not None else _wsl_targets()):
            try:
                cmd = ["wsl"] + (["-d", d] if d else []) + ["-e", "bash", "-c", script]
                r = subprocess.run(
                    cmd,
                    input=(block + "\n").encode("utf-8") if enable else None,
                    capture_output=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if r.returncode != 0:
                    if enable:
                        # _wsl_write_failure already carries the WSL(distro) label
                        errs.append(_wsl_write_failure(r, d))
                elif enable:
                    done.append(d)
            except Exception as e:
                if enable:
                    errs.append(f"WSL({d or ''}): {e}")
        if enable and errs:
            # 任一日标失败就对已成功写入的发行版执行反向清理：js_api 只在
            # 全部成功时记录 _wsl_written，漏回滚会让退出清理找不到这些
            # 发行版，代理块残留到下一次启用。
            for d in done:
                try:
                    subprocess.run(
                        ["wsl"] + (["-d", d] if d else [])
                        + ["-e", "bash", "-c", rollback],
                        capture_output=True, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    log_exception_once("wsl_rollback", "set_wsl_proxy rollback")
            return LJoin(errs)
        return True
    except Exception as e:
        # Disabling is best-effort cleanup: a missing/broken WSL has nothing
        # to clean, and reporting it would block the whole disable flow.
        if not enable:
            return True
        return str(e) or "WSL 操作失败"


def _git_win_get(key: str):
    """Current git global value for `key`, or None if unset / git missing."""
    try:
        r = subprocess.run(
            ["git", "config", "--global", "--get", key],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    v = (r.stdout or "").strip()
    return v or None


def _git_win_restore_key(key: str, prev) -> None:
    """Put `key` back to `prev` (None → unset all values)."""
    if prev:
        subprocess.run(
            ["git", "config", "--global", "--replace-all", key, prev],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.run(
            ["git", "config", "--global", "--unset-all", key],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)


def _git_win_unset(url: str) -> None:
    """Remove one exact git proxy URL on Windows. --fixed-value needs Git
    2.33+; fall back to a regex unset so older Git still disables. The
    regex is anchored (^...$) — an unanchored pattern would also match
    values that merely start with the URL (789 → 7890)."""
    for key in ("http.proxy", "https.proxy"):
        r = subprocess.run(
            ["git", "config", "--global", "--fixed-value", "--unset-all", key, url],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode in (0, 5):
            continue
        subprocess.run(
            ["git", "config", "--global", "--unset-all", key,
             "^" + re.escape(url) + "$"],
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
    regex-escaped string with ``_bash_sq`` to close that hole. The pattern
    is anchored (^...$) so unsetting ``:789`` cannot also remove ``:7890``."""
    esc = _bash_sq("^" + re.escape(url) + "$")
    sq = _bash_sq(url)
    return (
        f"(git config --global --fixed-value --unset-all http.proxy '{sq}' "
        f"|| git config --global --unset-all http.proxy '{esc}') 2>/dev/null; "
        f"(git config --global --fixed-value --unset-all https.proxy '{sq}' "
        f"|| git config --global --unset-all https.proxy '{esc}') 2>/dev/null; true"
    )


def _wsl_git_config_get(distro, key):
    """Current `git config --global` value of `key` inside one distro, or
    None when the key is unset. `key` is a literal from this module
    ("http.proxy" / "https.proxy"), never user input.

    `git config --get` exits 1 for a missing key and 0 for a present one, so
    a returncode in (0, 1) is a trustworthy answer. Anything else — git
    missing (127), a wsl.exe failure, a timeout — is an UNKNOWN state and
    MUST raise: reporting it as "unset" would let the caller overwrite the
    user's proxy without ever having snapshotted it, which is exactly what
    the multi-distro rollback snapshot exists to prevent. The OSError lands
    in the callers' per-distro handler, which fails that distro instead of
    writing to it."""
    where = f"WSL({distro}): " if distro else ""
    try:
        r = subprocess.run(
            ["wsl"] + (["-d", distro] if distro else [])
            + ["-e", "bash", "-c", f"git config --global --get {key}"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        raise OSError(f"{where}git config --get {key}: {e}") from e
    if r.returncode == 1:
        return None
    if r.returncode != 0:
        raise OSError(LStr("errGitFailWslWhere",
                           {"where": where, "code": r.returncode}))
    return (r.stdout or "").strip()


def _wsl_git_config_set(distro, key, value):
    """Write `value` into one distro's git global config; an empty value
    unsets the key (the state a key the user never set must come back as).
    Returns True, or raises OSError so a failed rollback is logged instead of
    silently leaving this tool's URL in the distro.

    `--unset-all` exits 5 when the key was already absent — the desired end
    state — so (0, 5) counts as done there; a replace-all must exit 0."""
    if value:
        script = f"git config --global --replace-all {key} '{_bash_sq(value)}'"
        ok = (0,)
    else:
        script = f"git config --global --unset-all {key}"
        ok = (0, 5)
    r = subprocess.run(
        ["wsl"] + (["-d", distro] if distro else []) + ["-e", "bash", "-c", script],
        capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode not in ok:
        where = f"WSL({distro}): " if distro else ""
        raise OSError(LStr("errGitFailWslWhere",
                           {"where": where, "code": r.returncode}))
    return True


def set_git_proxy(host, port, enable=True, use_wsl=False,
                  scheme="http", user="", password="", distros=None):
    """Configure git's global http.proxy/https.proxy, either on Windows
    (use_wsl=False) or inside WSL (use_wsl=True).

    Host/port/userinfo are pre-validated by the UI, so the URL is injection-safe.
    Disabling is best-effort: a missing git binary (or missing WSL) simply
    means there is nothing to clean up and is reported as success, so it
    never blocks the unconditional disable flow.

    `distros` is an explicit target list for the WSL side (callers reaching
    distros the current scope no longer covers); None keeps the scope-derived
    _wsl_targets() behavior. Ignored when use_wsl=False."""
    url = build_proxy_url(host, port, scheme, user, password)
    try:
        if use_wsl:
            errs = []
            done = []
            targets = list(distros) if distros is not None else list(_wsl_targets())
            # 多发行版：任一目标写入失败都要回滚已写入的发行版，而回滚必须写回
            # 用户原值（不是只卸掉本工具的 URL）——覆盖前先按发行版快照。
            # 快照读失败（OSError）进 except → errs，该发行版不写入。
            # 单目标时回滚循环不可能执行（要么全成功、要么 done 为空），
            # 省掉每个发行版两次 wsl.exe 读取，命令序列与从前逐字一致。
            snap = {} if enable and len(targets) > 1 else None
            for d in targets:
                where = f"WSL({d}): " if d else ""
                try:
                    if snap is not None:
                        snap[d] = {
                            "http.proxy": _wsl_git_config_get(d, "http.proxy"),
                            "https.proxy": _wsl_git_config_get(d, "https.proxy"),
                        }
                    if enable:
                        sq = _bash_sq(url)
                        # https 失败时把 http.proxy 恢复成写入前的值（没有则
                        # unset），避免半开，也避免把用户原来的代理删掉。
                        cmd = (
                            "old_http=$(git config --global --get http.proxy 2>/dev/null || true); "
                            f"git config --global --replace-all http.proxy '{sq}' && "
                            f"(git config --global --replace-all https.proxy '{sq}' || "
                            "{ if [ -n \"$old_http\" ]; then "
                            "git config --global --replace-all http.proxy \"$old_http\"; "
                            "else git config --global --unset-all http.proxy 2>/dev/null; fi; "
                            "exit 1; })"
                        )
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
                            errs.append(LStr("errNoWslWhere", {"where": where}))
                        elif r.returncode == 127:
                            errs.append(LStr("errNoGitWslWhere", {"where": where}))
                        else:
                            errs.append(LStr("errGitFailWslWhere",
                                             {"where": where, "code": r.returncode}))
                    elif enable:
                        done.append(d)
                except Exception as e:
                    if enable:
                        errs.append(f"{where}{e}")
            if enable and errs:
                # 任一日标失败就对已写入的发行版回滚到快照里的用户原值
                # （启用前没有该键就 unset）：js_api 只在全部成功时记录
                # _wsl_written，漏回滚、或只卸掉本工具的 URL，都会把用户
                # 原来的代理配置一并丢掉。
                for d in done:
                    try:
                        if snap is None:
                            subprocess.run(
                                ["wsl"] + (["-d", d] if d else [])
                                + ["-e", "bash", "-c", _git_wsl_unset_cmd(url)],
                                capture_output=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            for key in ("http.proxy", "https.proxy"):
                                _wsl_git_config_set(
                                    d, key, (snap.get(d) or {}).get(key))
                    except Exception:
                        log_exception_once("gitwsl_rollback", "set_git_proxy rollback")
                return LJoin(errs)
        else:
            if enable:
                # Snapshot first so a failed https.proxy write can put http.proxy
                # back to the user's value instead of --unset-all (which dropped
                # a proxy this tool had already replaced).
                prev = {key: _git_win_get(key)
                        for key in ("http.proxy", "https.proxy")}
                written = []
                for key in ("http.proxy", "https.proxy"):
                    r = subprocess.run(
                        ["git", "config", "--global", "--replace-all", key, url],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    if r.returncode != 0:
                        for k in written:
                            _git_win_restore_key(k, prev.get(k))
                        return (r.stderr or f"exit {r.returncode}").strip() \
                            or f"exit {r.returncode}"
                    written.append(key)
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
        return LStr("errNoGitInWsl") if use_wsl else LStr("errNoGit")
    except Exception as e:
        if not enable:
            return True
        return str(e)


def get_git_proxy_win():
    """Read git's global http.proxy on Windows, falling back to https.proxy
    ('未设置' when unset, '未检测到' when git is unavailable)."""
    try:
        r = subprocess.run(["git", "config", "--global", "--get", "http.proxy"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        v = r.stdout.strip()
        if v:
            return v
        r = subprocess.run(["git", "config", "--global", "--get", "https.proxy"],
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


_INI_COMMENT_RE = re.compile(r"(\s|^)[#;]")


def _ini_value_comment(raw: str):
    """Split an INI value into (value, trailing comment).

    npm's .npmrc and pip's pip.ini both treat `#` / `;` as comment markers,
    so `proxy = http://host:port  # corp` must compare (and rewrite) as the
    URL alone. The marker only counts at the start of the value or after
    whitespace, so a value that legitimately contains '#' is not truncated.
    The comment is returned WITHOUT its leading whitespace."""
    m = _INI_COMMENT_RE.search(raw)
    if not m:
        return raw, ""
    at = m.start() + len(m.group(1))
    return raw[:at], raw[at:]


def _ini_strip_value(raw: str):
    """Value of an INI value-part: inline comment removed, quotes stripped."""
    value, _comment = _ini_value_comment(raw)
    return value.strip().strip('"').strip("'") or None


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
        return _ini_strip_value(m.group(1))
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
            return _ini_strip_value(m.group(1))
    return None


def _ini_edit(text, keys, url, equals, section=None, eol="\n"):
    """Rewrite `keys` lines in INI text: set them to `url`, or (url=None)
    remove each line whose value equals `equals` — surgical removal, so a
    user-set proxy for a different endpoint survives a disable. With a
    `section`, only lines INSIDE it are touched (pip reads proxy under
    [global]; an [install] proxy belongs to another tool and must survive
    verbatim). Keys absent from the section are appended right after its
    header (created when missing), else at the end (npm's .npmrc is flat).
    Every other line, key style (`:` vs `=`), quoting, trailing comment and
    line ending is preserved; appended lines use `eol` (the file's own
    dominant ending). A replaced line keeps its inline comment.
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
        value_part, comment = _ini_value_comment(m.group(4))
        cur = value_part.strip().strip('"').strip("'")
        if url is None:
            if cur and cur != equals:
                out.append(line)  # user's own value: untouched
                continue
            continue              # our line: dropped entirely
        q = value_part[:1] if value_part[:1] in "\"'" else ""
        # 行尾注释原样保留：启用替换只换值，不吞掉用户写在这一行上的注释
        tail = f" {comment}{line[len(body):]}" if comment \
            else line[len(body):]
        out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{q}{url}{q}{tail}")
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
    """Decode INI file bytes as (text, encoding). Tries UTF-8 (BOM optional),
    then UTF-16 (BOM-detected) then the ANSI codepage — a .npmrc / pip.conf
    hand-saved with GBK comments must not make the WSL reads/writes raise
    UnicodeDecodeError (which is not an OSError, so it slipped past every
    endpoint's error handling). The returned encoding is reused on write so
    the file round-trips ('utf-16' re-emits the BOM on encode)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
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
    return _ini_get(text, "proxy") or _ini_get(text, "https-proxy") or UNSET


def _atomic_write_text(path, text, encoding="utf-8"):
    """Write `text` to `path` via tmp + os.replace so a mid-write failure
    (disk full, AV lock) cannot truncate the original file. Same contract
    as config_store.save_config. newline="" keeps \\r\\n verbatim."""
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding=encoding, newline="", errors="replace") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
            # Always plain UTF-8: it's the only encoding npm reads.
            _atomic_write_text(NPM_CONFIG_PATH, new, encoding="utf-8")
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
            _atomic_write_text(path, new, encoding=enc)
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
        raise OSError(LStr("errWslReadFail", {
            "where": f"WSL({distro}): " if distro else "",
            "path": shown, "code": r.returncode}))
    return r.stdout


def _wsl_push_script(path: str) -> str:
    """Bash that replaces `path` with stdin without truncating the target first.

    `cat "$tmp" > "$path"` empties the destination before the copy finishes, so
    a full disk or a killed wsl.exe leaves ~/.npmrc / pip.conf as a 0-byte
    file. mktemp in the same directory + `mv -f` is atomic on the same fs
    (same contract as Windows `_atomic_write_text`). A cat-to-tmp failure
    deletes the temp file and never touches the original.
    """
    return (
        f'dir=$(dirname "{path}"); '
        f'mkdir -p "$dir" || exit 1; '
        f'tmp=$(mktemp "$dir/.pmXXXXXX") || exit 1; '
        f'cat > "$tmp" || {{ rm -f "$tmp"; exit 1; }}; '
        f'mv -f "$tmp" "{path}" || {{ rm -f "$tmp"; exit 1; }}'
    )


def _wsl_push_file(distro, path, data: bytes):
    """Atomically replace a file in a distro with `data` (arrives on stdin)."""
    shown = path
    path = _bash_path(path)
    script = _wsl_push_script(path)
    r = subprocess.run(
        ["wsl"] + (["-d", distro] if distro else []) + ["-e", "bash", "-c", script],
        input=data, capture_output=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise OSError(LStr("errWslWriteFileFail", {
            "where": f"WSL({distro}): " if distro else "",
            "path": shown, "code": r.returncode}))


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
    try:
        payload = new.encode(enc)
    except (UnicodeEncodeError, LookupError):
        # mbcs 对 decode 时的 U+FFFD 或区域外字符会 strict 失败，令关闭路径
        # 静默"成功"而代理 URL 原样留在发行版里。回退 UTF-8 按字节写回。
        payload = new.encode("utf-8")
    _wsl_push_file(distro, path, payload)
    return True


def _wsl_ini_snapshot(distro, path, keys, section=None):
    """Current values of `keys` in one distro's config file, taken BEFORE
    this tool overwrites them. Only keys that really have a value are
    returned — a key the user never set must come back as absent, never as
    an invented value — and a file that doesn't exist yields {}. Raises
    OSError when the distro cannot be read, so the caller fails that distro
    instead of overwriting a proxy it never snapshotted (same contract as
    _wsl_git_config_get)."""
    data = _wsl_pull_file(distro, path)
    if data is None:
        return {}
    text = _decode_ini_bytes(data)[0]
    out = {}
    for key in keys:
        v = _ini_get(text, key, section)
        if v:
            out[key] = v
    return out


def _wsl_ini_restore(distro, path, keys, snap, equals, section=None):
    """Put one distro's config file back to its pre-enable state, key by key:
    a snapshotted value is written back (its trailing comment survives,
    _ini_edit only rewrites the value), a key without one has our line
    removed. Surgical — a value the user set for another endpoint is not
    equal to `equals`, so it is never touched."""
    for key in keys:
        _wsl_edit_ini(distro, path, (key,), (snap or {}).get(key),
                      equals=equals, section=section)


def get_npm_proxy_wsl(distro=None):
    """npm proxy inside the distro(s). Reads every user distro and returns
    the first proxy found (multi-distro setups are rare and usually kept in
    sync; the badge shows one URL). '未设置' / '未检测到' as elsewhere.

    A broken distro must not abort the walk: continue to the next one and
    only report 未检测到 when at least one pull failed and nothing was found."""
    try:
        any_err = False
        for d in (_wsl_targets()):
            try:
                data = _wsl_pull_file(d, _NPMrc_PATH_WSL)
            except OSError:
                any_err = True
                continue
            if data is None:
                continue
            text = _decode_ini_bytes(data)[0]
            v = _ini_get(text, "proxy") or _ini_get(text, "https-proxy")
            if v:
                return v
        return UNDETECTED if any_err else UNSET
    except Exception:
        log_exception("get_npm_proxy_wsl")
        return UNDETECTED


def get_pip_proxy_wsl(distro=None):
    """pip proxy inside the distro(s): XDG pip.conf first, then the legacy
    ~/.pip/pip.conf (both are read by pip itself, in that order).

    Same continue-on-error contract as get_npm_proxy_wsl."""
    try:
        any_err = False
        for d in (_wsl_targets()):
            for path in (_PIP_CONF_PATH_WSL, _PIP_CONF_LEGACY_WSL):
                try:
                    data = _wsl_pull_file(d, path)
                except OSError:
                    any_err = True
                    continue
                if data is None:
                    continue
                v = _ini_get(_decode_ini_bytes(data)[0], "proxy",
                             section="global")
                if v:
                    return v
        return UNDETECTED if any_err else UNSET
    except Exception:
        log_exception("get_pip_proxy_wsl")
        return UNDETECTED


def set_npm_proxy_wsl(host, port, enable=True, scheme="http", user="", password="",
                      distros=None):
    """Write/clear npm's proxy + https-proxy in every user distro's ~/.npmrc.
    Same disable contract as the Windows npm endpoint.

    Host is used as-is: NAT loopback→gateway rewrite happens in js_api
    (_wsl_url_target) so the written URL and the wsl_written record share
    one gateway probe. Passing a loopback host writes 127.0.0.1 literally.

    `distros` is an explicit target list for callers reaching distros the
    current scope no longer covers; None keeps the scope-derived behavior."""
    write_host = host
    url = build_proxy_url(write_host, port, scheme, user, password)
    try:
        errs = []
        done = []
        targets = list(distros) if distros is not None else list(_wsl_targets())
        # 多发行版：回滚要写回用户原值，覆盖前先按发行版快照。单发行版时
        # 回滚循环不可能执行（要么全成功、要么 done 为空），省掉一次读取，
        # 命令序列与从前逐字一致。
        snap = {} if enable and len(targets) > 1 else None
        for d in targets:
            try:
                if snap is not None:
                    snap[d] = _wsl_ini_snapshot(d, _NPMrc_PATH_WSL, NPM_PROXY_KEYS)
                _wsl_edit_ini(d, _NPMrc_PATH_WSL, NPM_PROXY_KEYS,
                              url if enable else None, equals=url)
                if enable:
                    done.append(d)
            except Exception as e:
                # TimeoutExpired 不是 OSError：只捕 OSError 会让超时跳过
                # 下面的回滚循环，已写入的发行版带着本工具 URL 却无记录。
                if enable:
                    errs.append(e)
        if errs:
            # 任一日标失败就对已成功写入的发行版回滚到快照里的用户原值：
            # js_api 只在全部成功时记录 _wsl_written，漏回滚、或只卸掉本工具
            # 的 URL，都会把用户原来的 .npmrc 代理一并丢掉。
            for d in done:
                try:
                    if snap is None:
                        _wsl_edit_ini(d, _NPMrc_PATH_WSL, NPM_PROXY_KEYS,
                                      None, equals=url)
                    else:
                        _wsl_ini_restore(d, _NPMrc_PATH_WSL, NPM_PROXY_KEYS,
                                         snap.get(d), equals=url)
                except Exception:
                    log_exception_once("npmwsl_rollback", "set_npm_proxy_wsl rollback")
        if enable and errs:
            return LJoin(errs)
        return True
    except Exception:
        log_exception("set_npm_proxy_wsl")
        return True if not enable else "WSL 操作失败"


def set_pip_proxy_wsl(host, port, enable=True, scheme="http", user="", password="",
                      distros=None):
    """Write/clear `proxy` under [global] in every user distro's XDG
    pip.conf; disable also cleans the legacy ~/.pip/pip.conf.

    Host is used as-is (see set_npm_proxy_wsl): NAT rewrite is done once in
    js_api before the call. `distros` is an explicit target list for callers
    reaching distros the current scope no longer covers."""
    write_host = host
    url = build_proxy_url(write_host, port, scheme, user, password)
    try:
        errs = []
        done = []
        targets = list(distros) if distros is not None else list(_wsl_targets())
        # 多发行版：回滚要写回用户原值，覆盖前先按发行版快照（同 npm）。
        snap = {} if enable and len(targets) > 1 else None
        for d in targets:
            try:
                if snap is not None:
                    snap[d] = _wsl_ini_snapshot(d, _PIP_CONF_PATH_WSL,
                                                ("proxy",), section="global")
                _wsl_edit_ini(d, _PIP_CONF_PATH_WSL, ("proxy",),
                              url if enable else None, equals=url,
                              section="global")
                if not enable:
                    # Legacy path: surgical clean only, never written.
                    _wsl_edit_ini(d, _PIP_CONF_LEGACY_WSL, ("proxy",),
                                  None, equals=url)
                if enable:
                    done.append(d)
            except Exception as e:
                # 同 npm：TimeoutExpired 不是 OSError，必须进 errs 触发回滚。
                if enable:
                    errs.append(e)
        if errs:
            # 任一日标失败就对已成功写入的发行版回滚到快照里的用户原值（只回滚
            # 启用真正写过的 XDG 路径，legacy 路径启用时从未写过）。
            for d in done:
                try:
                    if snap is None:
                        _wsl_edit_ini(d, _PIP_CONF_PATH_WSL, ("proxy",),
                                      None, equals=url, section="global")
                    else:
                        _wsl_ini_restore(d, _PIP_CONF_PATH_WSL, ("proxy",),
                                         snap.get(d), equals=url,
                                         section="global")
                except Exception:
                    log_exception_once("pipwsl_rollback", "set_pip_proxy_wsl rollback")
        if enable and errs:
            return LJoin(errs)
        return True
    except Exception:
        log_exception("set_pip_proxy_wsl")
        return True if not enable else "WSL 操作失败"


def restart_wsl():
    try:
        subprocess.run(["wsl", "--shutdown"],
                       capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        # The distro list can change with a restart (installs/removals while
        # down), and every caller caches the enumeration for a short TTL.
        invalidate_wsl_distros_cache()
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
        return LStr("errRestartNoWsl", {"err": last_err or "?"})
    except Exception as e:
        return str(e)
