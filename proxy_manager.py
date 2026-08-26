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
import sys
import socket
import threading
import ctypes
from typing import Optional

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

import pystray
from PIL import Image, ImageDraw

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

HWND_BROADCAST   = 0xFFFF
WM_SETTINGCHANGE = 0x001A

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
APP_HEIGHT = 480
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


# ─── Config ───────────────────────────────────────────────────────────────────
def load_config():
    defaults = {"host": DEFAULT_HOST, "port": DEFAULT_PORT,
                "powershell": True, "wsl": True, "pin": False, "mode": "light"}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            defaults.update(cfg)          # backfill any new keys
            return defaults
        except Exception:
            pass
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
            capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW
        )
        ip = r.stdout.strip()
        return ip if ip else "未获取到"
    except Exception:
        return "WSL 未运行"


_wsl_network_mode_cache: Optional[str] = None


def get_wsl_network_mode():
    global _wsl_network_mode_cache
    if _wsl_network_mode_cache is not None:
        return _wsl_network_mode_cache
    try:
        p = os.path.expanduser(r"~\.wslconfig")
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
                        _wsl_network_mode_cache = m.group(1).capitalize()
                        return _wsl_network_mode_cache
        _wsl_network_mode_cache = "默认(NAT)"
        return _wsl_network_mode_cache
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
            capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW
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
        else:
            for n in ("HTTP_PROXY", "HTTPS_PROXY"):
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


def set_wsl_proxy(host, port, enable=True):
    url = f"http://{host}:{port}"
    block = f"""{WSL_PROXY_BEGIN}
export HTTP_PROXY="{url}"
export HTTPS_PROXY="{url}"
export http_proxy="{url}"
export https_proxy="{url}"
{WSL_PROXY_END}"""
    try:
        # Always remove any prior managed block first (idempotent).
        sed = f"sed -i '/{re.escape(WSL_PROXY_BEGIN)}/,/{re.escape(WSL_PROXY_END)}/d' ~/.bashrc"
        subprocess.run(["wsl", "-e", "bash", "-c", sed],
                       capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        if enable:
            # Feed the block via stdin so no shell interpolation touches it —
            # avoids quoting/escaping pitfalls with arbitrary host strings.
            subprocess.run(["wsl", "-e", "bash", "-c", "cat >> ~/.bashrc"],
                           input=block + "\n", text=True,
                           capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception as e:
        return str(e)


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
def _make_tray_image():
    """Draw a simple 64x64 ring icon (accent-colored ring, white center)."""
    hex_color = C["accent"].lstrip("#")
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill=rgb + (255,))          # outer ring
    d.ellipse([22, 22, 42, 42], fill=(255, 255, 255, 255))  # inner disc
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
        # Skip placeholders / failure strings — only copy things that look
        # like an address (contains a digit, no spaces).
        if not text or any(c.isspace() for c in text) or not any(c.isdigit() for c in text):
            return
        try:
            self._root.clipboard_clear()
            self._root.clipboard_append(text)
        except Exception:
            return
        # Brief visual confirmation.
        self._val.configure(text=f"{text}  ✓ 已复制", text_color=C["green"])
        self._val.after(1200, lambda: self._val.configure(text=text, text_color=C["text"]))


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

        # Rebuild generation — incremented each time _build() tears down and
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
        self.root.minsize(APP_WIDTH, 400)

        self._async_refresh()
        self._speed_prev = get_net_counters()
        if HAVE_PSUTIL:
            self._tick_speed()

        # Closing the window hides to the tray instead of quitting.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Safe after() ─────────────────────────────────────────────────────────────
    def _after(self, delay_ms, func):
        """Schedule func on the main loop, but only run it if no rebuild has
        happened since scheduling. Prevents stale callbacks from touching
        destroyed widgets."""
        gen = self._generation
        def wrapper():
            if gen == self._generation:
                func()
        self.root.after(delay_ms, wrapper)

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
        ctk.CTkLabel(left, text="PowerShell & WSL 一键代理切换",
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
                                 text_color="#CCC", width=36)
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
        self._speed_up = self._speed_dn = None
        if HAVE_PSUTIL:
            iface = _primary_iface or "—"
            ctk.CTkLabel(nc, text=f"实时网速 ({iface})",
                         font=ctk.CTkFont(family=FONT_CN, size=11, weight="bold"),
                         text_color=C["text2"]).pack(anchor="w", padx=16, pady=(6, 2))
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
        ctk.CTkLabel(pc, text="PowerShell",
                     font=ctk.CTkFont(family=FONT_EN, size=10, weight="bold"),
                     text_color=C["text2"]).pack(anchor="w", padx=16, pady=(0, 2))
        self._ps_http = InfoRow(pc, "HTTP_PROXY")
        self._ps_http.pack(fill="x", padx=16, pady=(0, 2))
        self._ps_https = InfoRow(pc, "HTTPS_PROXY")
        self._ps_https.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(pc, text="WSL",
                     font=ctk.CTkFont(family=FONT_EN, size=10, weight="bold"),
                     text_color=C["text2"]).pack(anchor="w", padx=16, pady=(0, 2))
        self._wsl_http = InfoRow(pc, "HTTP_PROXY")
        self._wsl_http.pack(fill="x", padx=16, pady=(0, 2))
        self._wsl_https = InfoRow(pc, "HTTPS_PROXY")
        self._wsl_https.pack(fill="x", padx=16, pady=(0, 12))

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

        chk = ctk.CTkFrame(cc, fg_color="transparent")
        chk.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkCheckBox(chk, text="PowerShell", variable=self.ps_var,
                        font=ctk.CTkFont(family=FONT_CN, size=11), corner_radius=6,
                        border_color=C["border"], fg_color=C["accent"],
                        hover_color=C["accent_h"]).pack(side="left")
        ctk.CTkCheckBox(chk, text="WSL", variable=self.wsl_var,
                        font=ctk.CTkFont(family=FONT_CN, size=11), corner_radius=6,
                        border_color=C["border"], fg_color=C["accent"],
                        hover_color=C["accent_h"]).pack(side="left", padx=(18, 0))

    def _build_buttons(self, parent):
        bf = ctk.CTkFrame(parent, fg_color="transparent")
        bf.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 12))
        bf.grid_columnconfigure((0, 1, 2), weight=1)

        self._en = ctk.CTkButton(
            bf, text="启用代理", height=42,
            font=ctk.CTkFont(family=FONT_CN, size=14, weight="bold"),
            corner_radius=12, fg_color=C["accent"], hover_color=C["accent_h"],
            text_color="#FFFFFF", command=self._enable)
        self._en.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self._dis = ctk.CTkButton(
            bf, text="关闭代理", height=42,
            font=ctk.CTkFont(family=FONT_CN, size=14, weight="bold"),
            corner_radius=12, fg_color="transparent", border_width=2,
            border_color=C["red"], text_color=C["red"],
            hover_color=C["danger_hover"], command=self._disable)
        self._dis.grid(row=0, column=1, padx=6, sticky="ew")

        self._rst = ctk.CTkButton(
            bf, text="重启 WSL", height=42,
            font=ctk.CTkFont(family=FONT_CN, size=14, weight="bold"),
            corner_radius=12, fg_color=C["secondary"], hover_color=C["secondary_h"],
            text_color="#FFFFFF", command=self._restart)
        self._rst.grid(row=0, column=2, padx=(6, 0), sticky="ew")

    # ── Async Refresh ──
    def _async_refresh(self):
        self._refresh.configure(state="disabled")
        self._status.configure(text="检测中…")
        self._dot.configure(text_color="#CCC")

        def work():
            # Run independent I/O-bound checks in parallel. The three WSL
            # subprocess calls used to run serially (1.5-6s); now they overlap
            # and the total is bounded by the slowest single call (~0.5-2s).
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=5) as pool:
                f_ps  = pool.submit(get_ps_status)
                f_li  = pool.submit(get_local_ip)
                f_wi  = pool.submit(get_wsl_ip)
                f_mo  = pool.submit(get_wsl_network_mode)
                f_psp = pool.submit(get_ps_proxy_vars)
                # Single WSL read shared by both status and proxy-var parsing.
                f_block = pool.submit(_read_wsl_block)
                ps  = f_ps.result()
                li  = f_li.result()
                wi  = f_wi.result()
                mo  = f_mo.result()
                psp = f_psp.result()
                block = f_block.result()
            ws  = get_wsl_status(block)
            wsp = get_wsl_proxy_vars(block)
            self._after(0, lambda: self._update(ps, ws, li, wi, mo, psp, wsp))

        threading.Thread(target=work, daemon=True).start()

    def _update(self, ps, ws, li, wi, mo, psp, wsp):
        if ps or ws:
            self._dot.configure(text_color=C["green"])
            self._status.configure(text="已启用")
        else:
            self._dot.configure(text_color="#CCC")
            self._status.configure(text="未启用")
        self._lip.set_value(li)
        self._wip.set_value(wi)
        self._wmode.set_value(mo)
        self._ps_http.set_value(psp.get("HTTP_PROXY", "未设置"))
        self._ps_https.set_value(psp.get("HTTPS_PROXY", "未设置"))
        self._wsl_http.set_value(wsp.get("HTTP_PROXY", "未设置"))
        self._wsl_https.set_value(wsp.get("HTTPS_PROXY", "未设置"))
        self._refresh.configure(state="normal")

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
        if not p.isdigit() or not (1 <= int(p) <= 65535):
            messagebox.showwarning("提示", "端口须为 1-65535 的整数")
            return None
        if not self.ps_var.get() and not self.wsl_var.get():
            messagebox.showwarning("提示", "请至少选择一个目标")
            return None
        return h, str(int(p))

    # ── Enable ──
    def _enable(self):
        r = self._validate()
        if not r:
            return
        h, p = r
        save_config(h, p, self.ps_var.get(), self.wsl_var.get(),
                    self.pin_var.get(), self._mode)
        errs = []
        if self.ps_var.get():
            ret = set_ps_proxy(h, p, True)
            if ret is not True:
                errs.append(f"PowerShell: {ret}")
        if self.wsl_var.get():
            ret = set_wsl_proxy(h, p, True)
            if ret is not True:
                errs.append(f"WSL: {ret}")
        if errs:
            self._async_refresh()
            messagebox.showerror("错误", "\n".join(errs))
            return
        if (self.wsl_var.get()
                and messagebox.askyesno(
                    "WSL 重启",
                    "代理已注入，WSL 需重启生效。\n\n立即重启？\n（会终止 WSL 中所有进程）")):
            self._restart()
        else:
            self._async_refresh()

    # ── Disable ──
    def _disable(self):
        errs = []
        if self.ps_var.get():
            ret = set_ps_proxy(DEFAULT_HOST, DEFAULT_PORT, False)
            if ret is not True:
                errs.append(f"PowerShell: {ret}")
        if self.wsl_var.get():
            ret = set_wsl_proxy(DEFAULT_HOST, DEFAULT_PORT, False)
            if ret is not True:
                errs.append(f"WSL: {ret}")
        if errs:
            self._async_refresh()
            messagebox.showerror("错误", "\n".join(errs))
            return
        if (self.wsl_var.get()
                and messagebox.askyesno(
                    "WSL 重启",
                    "代理已移除，WSL 需重启生效。\n\n立即重启？")):
            self._restart()
        else:
            self._async_refresh()

    # ── Restart WSL ──
    def _restart(self):
        self._rst.configure(state="disabled", text="重启中…")

        def do():
            ret = restart_wsl()
            self._after(0, lambda: self._rst_done(ret))

        threading.Thread(target=do, daemon=True).start()

    def _rst_done(self, ret):
        self._rst.configure(state="normal", text="重启 WSL")
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
        self.root.configure(fg_color=C["bg"])
        save_config(self.host_var.get().strip(), self.port_var.get().strip(),
                    self.ps_var.get(), self.wsl_var.get(), self.pin_var.get(), self._mode)
        # Re-tint the existing widget tree in place — no destroy/rebuild, so the
        # switch is instant with no visible flicker.
        for w in self.root.winfo_children():
            if getattr(w, "_is_content", False):
                self._apply_colors(w)
        self._sync_pin_btn()

    def _apply_colors(self, widget):
        """Recursively restyle an existing widget tree to the active palette
        without destroying any widgets. customtkinter supports dynamic color
        updates via .configure()."""
        if isinstance(widget, ctk.CTkButton):
            # Distinguish the three button styles by their role.
            text = widget.cget("text")
            if text == "启用代理":
                widget.configure(fg_color=C["accent"], hover_color=C["accent_h"],
                                 text_color="#FFFFFF", border_color=C["border"])
            elif text == "关闭代理":
                widget.configure(fg_color="transparent", border_color=C["red"],
                                 text_color=C["red"], hover_color=C["danger_hover"])
            elif text == "重启 WSL":
                widget.configure(fg_color=C["secondary"], hover_color=C["secondary_h"],
                                 text_color="#FFFFFF", border_color=C["border"])
            else:
                # Icon buttons (theme / pin / refresh).
                widget.configure(fg_color=C["input_bg"], hover_color=C["border"],
                                 text_color=C["text2"], border_color=C["border"])
        elif isinstance(widget, ctk.CTkLabel):
            # Section headers (bold, C["text"]) vs info labels (C["text2"]).
            weight = widget.cget("font").cget("weight") if hasattr(widget.cget("font"), "cget") else None
            if weight == "bold":
                widget.configure(text_color=C["text"])
            else:
                widget.configure(text_color=C["text2"])
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

    def _tick_speed(self):
        """Sample net counters every 1s and update the speed card."""
        cur = get_net_counters()
        prev = self._speed_prev
        if cur is not None and prev is not None and self._speed_up is not None:
            up = cur[0] - prev[0]
            dn = cur[1] - prev[1]
            # Guard against counter resets/overflows: a negative delta or an
            # implausibly large jump (>100 MB in one second) means the
            # underlying counter wrapped or the interface was re-enumerated;
            # skip this sample rather than showing a bogus spike.
            if 0 <= up <= self._MAX_DELTA:
                self._speed_up.set_value(format_speed(up))
            if 0 <= dn <= self._MAX_DELTA:
                self._speed_dn.set_value(format_speed(dn))
        self._speed_prev = cur if cur is not None else prev
        self.root.after(1000, self._tick_speed)

    # ── System Tray ──
    def _on_close(self):
        """Hide to tray instead of quitting."""
        self.root.withdraw()
        self._start_tray()

    def _start_tray(self):
        if self._tray_thread is not None:
            return
        try:
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

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self._show_window)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._quit)

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self):
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        self.root.destroy()


# ─── Entry ────────────────────────────────────────────────────────────────────
def main():
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
