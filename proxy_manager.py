"""
Proxy Manager - One-click proxy injection for PowerShell & WSL
Modern light UI with customtkinter
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

user32 = ctypes.WinDLL("user32")

# Explicit prototype for SendMessageTimeoutW — without this, 64-bit Python
# truncates pointer-sized args (lParam string pointer) and the WM_SETTINGCHANGE
# broadcast silently fails, so new terminals don't pick up the env change.
user32.SendMessageTimeoutW.argtypes = [
    ctypes.c_void_p,  # HWND       (HWND_BROADCAST = 0xFFFF)
    ctypes.c_uint,    # Msg        (WM_SETTINGCHANGE = 0x001A)
    ctypes.c_size_t,  # wParam     (0)
    ctypes.c_wchar_p, # lParam     ("Environment")
    ctypes.c_uint,    # fuFlags    (SMTO_ABORTIFUSED = 0x0002)
    ctypes.c_uint,    # uTimeout   (ms)
    ctypes.POINTER(ctypes.c_long),  # lpdwResult
]
user32.SendMessageTimeoutW.restype = ctypes.c_long  # LPARAM

HWND_BROADCAST   = 0xFFFF
WM_SETTINGCHANGE = 0x001A

# ─── Theme ───────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ─── Constants ───────────────────────────────────────────────────────────────
APP_TITLE  = "代理管理器"
APP_WIDTH  = 400
APP_HEIGHT = 680
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "7897"


def _config_dir() -> str:
    """Persist config under %APPDATA%\\ProxyManager so it survives PyInstaller
    --onefile runs (where __file__ points to a transient _MEIxxxxx dir)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ProxyManager")


CONFIG_FILE = os.path.join(_config_dir(), "config.json")

# Colors — soft, warm, harmonious
C_BG       = "#F0EFF4"   # lavender-tinted background
C_CARD     = "#FFFFFF"
C_ACCENT   = "#6E8EFB"   # soft periwinkle blue
C_ACCENT_H = "#5A7BF0"
C_GREEN    = "#4CD964"   # iOS-style green
C_GREEN_H  = "#3CC054"
C_RED      = "#FF6B6B"   # soft coral red
C_RED_H    = "#E85D5D"
C_TEXT     = "#2D2D2D"
C_TEXT2    = "#9999A0"
C_BORDER   = "#E8E8ED"
C_INPUT_BG = "#F7F7FA"

# Fonts
FONT_CN = "Microsoft YaHei UI"
FONT_EN = "Arial"

# WSL markers
WSL_PROXY_BEGIN = "# >>> proxy-manager begin >>>"
WSL_PROXY_END   = "# <<< proxy-manager end <<<"
ENV_REG_PATH    = r"Environment"


# ─── Config ──────────────────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"host": DEFAULT_HOST, "port": DEFAULT_PORT, "powershell": True, "wsl": True}


def save_config(host, port, powershell, wsl):
    try:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"host": host, "port": port, "powershell": powershell, "wsl": wsl},
                      f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ─── Network Info ────────────────────────────────────────────────────────────
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


def get_wsl_network_mode():
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
                        return m.group(1).capitalize()
            return "默认(NAT)"
        return "默认(NAT)"
    except Exception:
        return "未知"


# ─── Proxy Logic ─────────────────────────────────────────────────────────────
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


def get_wsl_proxy_vars():
    """Read HTTP_PROXY and HTTPS_PROXY produced by this tool from WSL."""
    result = {"HTTP_PROXY": "未设置", "HTTPS_PROXY": "未设置"}
    block = _read_wsl_block()
    if not block:
        return result
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        val = _parse_export(block, name)
        if val:
            result[name] = val
    return result


def get_wsl_status():
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
        # Broadcast env change so already-running processes (and new terminals)
        # pick up the updated values.
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


# ─── Info Row Widget ─────────────────────────────────────────────────────────
class InfoRow(ctk.CTkFrame):
    def __init__(self, master, label, value="", **kw):
        super().__init__(master, fg_color="transparent", **kw)
        ctk.CTkLabel(self, text=label,
                     font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C_TEXT2, width=85, anchor="w"
                     ).pack(side="left")
        self._val = ctk.CTkLabel(self, text=value,
                                 font=ctk.CTkFont(family=FONT_EN, size=11, weight="bold"),
                                 text_color=C_TEXT, anchor="w")
        self._val.pack(side="left", fill="x", expand=True)

    def set_value(self, t):
        self._val.configure(text=t)


# ─── Main App ────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.configure(fg_color=C_BG)

        # Make window resizable vertically
        self.root.resizable(False, True)

        cfg = load_config()
        self.host_var = ctk.StringVar(value=cfg["host"])
        self.port_var = ctk.StringVar(value=cfg["port"])
        self.ps_var   = ctk.BooleanVar(value=cfg["powershell"])
        self.wsl_var  = ctk.BooleanVar(value=cfg["wsl"])

        self._build()

        # Auto-size: fit content, cap at 85% screen height
        self.root.update_idletasks()
        req_h = self.root.winfo_reqheight()
        max_h = int(self.root.winfo_screenheight() * 0.85)
        final_h = min(req_h + 10, max_h)
        x = (self.root.winfo_screenwidth() - APP_WIDTH) // 2
        y = (self.root.winfo_screenheight() - final_h) // 2
        self.root.geometry(f"{APP_WIDTH}x{final_h}+{x}+{y}")
        self.root.minsize(APP_WIDTH, 400)

        self._async_refresh()

    # ── Build UI ──
    def _build(self):
        S = {"padx": 20, "pady": (0, 10)}

        # Scrollable container
        scroll = ctk.CTkScrollableFrame(
            self.root, fg_color="transparent",
            scrollbar_button_color=C_BORDER, scrollbar_button_hover_color=C_TEXT2
        )
        scroll.pack(fill="both", expand=True)

        # ── Title ──
        tf = ctk.CTkFrame(scroll, fg_color="transparent")
        tf.pack(fill="x", padx=4, pady=(10, 4))
        ctk.CTkLabel(tf, text="代理管理器",
                     font=ctk.CTkFont(family=FONT_CN, size=20, weight="bold"),
                     text_color=C_TEXT).pack(anchor="w")
        ctk.CTkLabel(tf, text="PowerShell & WSL 一键代理切换",
                     font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C_TEXT2).pack(anchor="w", pady=(2, 0))

        # ── Status ──
        sc = ctk.CTkFrame(scroll, corner_radius=14, fg_color=C_CARD,
                          border_width=1, border_color=C_BORDER)
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
                     text_color=C_TEXT2).pack(anchor="w")
        self._status = ctk.CTkLabel(stf, text="检测中…",
                                    font=ctk.CTkFont(family=FONT_CN, size=15, weight="bold"),
                                    text_color=C_TEXT)
        self._status.pack(anchor="w")

        self._refresh = ctk.CTkButton(
            si, text="⟳", width=34, height=34,
            font=ctk.CTkFont(size=16), corner_radius=17,
            fg_color=C_INPUT_BG, hover_color=C_BORDER,
            text_color=C_TEXT2, command=self._async_refresh)
        self._refresh.pack(side="right")

        # ── Network Info ──
        nc = ctk.CTkFrame(scroll, corner_radius=14, fg_color=C_CARD,
                          border_width=1, border_color=C_BORDER)
        nc.pack(fill="x", **S)

        ctk.CTkLabel(nc, text="网络信息",
                     font=ctk.CTkFont(family=FONT_CN, size=12, weight="bold"),
                     text_color=C_TEXT).pack(anchor="w", padx=16, pady=(12, 6))

        self._lip = InfoRow(nc, "本机 IP")
        self._lip.pack(fill="x", padx=16, pady=(0, 2))
        self._wip = InfoRow(nc, "WSL IP")
        self._wip.pack(fill="x", padx=16, pady=(0, 2))
        self._wmode = InfoRow(nc, "WSL 网络模式")
        self._wmode.pack(fill="x", padx=16, pady=(0, 12))

        # ── Proxy Variables ──
        pc = ctk.CTkFrame(scroll, corner_radius=14, fg_color=C_CARD,
                          border_width=1, border_color=C_BORDER)
        pc.pack(fill="x", **S)

        ctk.CTkLabel(pc, text="代理变量",
                     font=ctk.CTkFont(family=FONT_CN, size=12, weight="bold"),
                     text_color=C_TEXT).pack(anchor="w", padx=16, pady=(12, 6))

        # PowerShell section
        ctk.CTkLabel(pc, text="PowerShell",
                     font=ctk.CTkFont(family=FONT_EN, size=10, weight="bold"),
                     text_color=C_TEXT2).pack(anchor="w", padx=16, pady=(0, 2))
        self._ps_http = InfoRow(pc, "HTTP_PROXY")
        self._ps_http.pack(fill="x", padx=16, pady=(0, 2))
        self._ps_https = InfoRow(pc, "HTTPS_PROXY")
        self._ps_https.pack(fill="x", padx=16, pady=(0, 8))

        # WSL section
        ctk.CTkLabel(pc, text="WSL",
                     font=ctk.CTkFont(family=FONT_EN, size=10, weight="bold"),
                     text_color=C_TEXT2).pack(anchor="w", padx=16, pady=(0, 2))
        self._wsl_http = InfoRow(pc, "HTTP_PROXY")
        self._wsl_http.pack(fill="x", padx=16, pady=(0, 2))
        self._wsl_https = InfoRow(pc, "HTTPS_PROXY")
        self._wsl_https.pack(fill="x", padx=16, pady=(0, 12))

        # ── Config ──
        cc = ctk.CTkFrame(scroll, corner_radius=14, fg_color=C_CARD,
                          border_width=1, border_color=C_BORDER)
        cc.pack(fill="x", **S)

        ctk.CTkLabel(cc, text="代理配置",
                     font=ctk.CTkFont(family=FONT_CN, size=12, weight="bold"),
                     text_color=C_TEXT).pack(anchor="w", padx=16, pady=(12, 6))

        # Host
        hf = ctk.CTkFrame(cc, fg_color="transparent")
        hf.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(hf, text="地址", font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C_TEXT2, width=36).pack(side="left")
        self._host = ctk.CTkEntry(hf, textvariable=self.host_var,
                                  font=ctk.CTkFont(family=FONT_EN, size=13),
                                  corner_radius=10, height=36, border_width=1,
                                  border_color=C_BORDER, fg_color=C_INPUT_BG)
        self._host.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # Port
        pf = ctk.CTkFrame(cc, fg_color="transparent")
        pf.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(pf, text="端口", font=ctk.CTkFont(family=FONT_CN, size=11),
                     text_color=C_TEXT2, width=36).pack(side="left")
        self._port = ctk.CTkEntry(pf, textvariable=self.port_var,
                                  font=ctk.CTkFont(family=FONT_EN, size=13),
                                  corner_radius=10, height=36, border_width=1,
                                  border_color=C_BORDER, fg_color=C_INPUT_BG, width=110)
        self._port.pack(side="left", padx=(8, 0))

        # Checkboxes
        chk = ctk.CTkFrame(cc, fg_color="transparent")
        chk.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkCheckBox(chk, text="PowerShell", variable=self.ps_var,
                        font=ctk.CTkFont(family=FONT_CN, size=11), corner_radius=6,
                        border_color=C_BORDER, fg_color=C_ACCENT,
                        hover_color=C_ACCENT_H).pack(side="left")
        ctk.CTkCheckBox(chk, text="WSL", variable=self.wsl_var,
                        font=ctk.CTkFont(family=FONT_CN, size=11), corner_radius=6,
                        border_color=C_BORDER, fg_color=C_ACCENT,
                        hover_color=C_ACCENT_H).pack(side="left", padx=(18, 0))

        # ── Buttons ──
        bf = ctk.CTkFrame(scroll, fg_color="transparent")
        bf.pack(fill="x", padx=20, pady=(0, 8))

        self._en = ctk.CTkButton(
            bf, text="启用代理", height=42,
            font=ctk.CTkFont(family=FONT_CN, size=14, weight="bold"),
            corner_radius=12, fg_color=C_GREEN, hover_color=C_GREEN_H,
            command=self._enable)
        self._en.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self._dis = ctk.CTkButton(
            bf, text="关闭代理", height=42,
            font=ctk.CTkFont(family=FONT_CN, size=14, weight="bold"),
            corner_radius=12, fg_color=C_RED, hover_color=C_RED_H,
            command=self._disable)
        self._dis.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Restart
        self._rst = ctk.CTkButton(
            scroll, text="重启 WSL", height=38,
            font=ctk.CTkFont(family=FONT_CN, size=12),
            corner_radius=12, fg_color=C_ACCENT, hover_color=C_ACCENT_H,
            command=self._restart)
        self._rst.pack(fill="x", padx=20, pady=(0, 20))

    # ── Async Refresh ──
    def _async_refresh(self):
        self._refresh.configure(state="disabled")
        self._status.configure(text="检测中…")
        self._dot.configure(text_color="#CCC")

        def work():
            ps  = get_ps_status()
            ws  = get_wsl_status()
            li  = get_local_ip()
            wi  = get_wsl_ip()
            mo  = get_wsl_network_mode()
            psp = get_ps_proxy_vars()
            wsp = get_wsl_proxy_vars()
            self.root.after(0, lambda: self._update(ps, ws, li, wi, mo, psp, wsp))

        threading.Thread(target=work, daemon=True).start()

    def _update(self, ps, ws, li, wi, mo, psp, wsp):
        if ps or ws:
            self._dot.configure(text_color=C_GREEN)
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
            messagebox.showwarning("提示", "请填写代理地址"); return None
        # Host: reject whitespace/control chars that would break URLs.
        if any(c.isspace() for c in h):
            messagebox.showwarning("提示", "代理地址不能包含空格"); return None
        # Port: accept decimal digits only and normalize away leading zeros /
        # signs so we don't emit URLs like "http://h:+8080" or "http://h:0070".
        if not p.isdigit() or not (1 <= int(p) <= 65535):
            messagebox.showwarning("提示", "端口须为 1-65535 的整数"); return None
        if not self.ps_var.get() and not self.wsl_var.get():
            messagebox.showwarning("提示", "请至少选择一个目标"); return None
        return h, str(int(p))

    # ── Enable ──
    def _enable(self):
        r = self._validate()
        if not r:
            return
        h, p = r
        save_config(h, p, self.ps_var.get(), self.wsl_var.get())
        errs = []
        if self.ps_var.get():
            ret = set_ps_proxy(h, p, True)
            if ret is not True:
                errs.append(f"PowerShell: {ret}")
        if self.wsl_var.get():
            ret = set_wsl_proxy(h, p, True)
            if ret is not True:
                errs.append(f"WSL: {ret}")
        # Stop on errors before any refresh/restart so the UI reflects the
        # failure rather than racing with a half-applied state.
        if errs:
            self._async_refresh()
            messagebox.showerror("错误", "\n".join(errs))
            return
        # Ask about WSL restart first; if declined, refresh once at the end.
        # This ordering avoids overlapping _async_refresh calls (which caused
        # status flicker / reading WSL mid-shutdown).
        if (self.wsl_var.get()
                and messagebox.askyesno(
                    "WSL 重启",
                    "代理已注入，WSL 需重启生效。\n\n立即重启？\n（会终止 WSL 中所有进程）")):
            self._restart()  # _restart triggers _async_refresh on completion
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
            self.root.after(0, lambda: self._rst_done(ret))

        threading.Thread(target=do, daemon=True).start()

    def _rst_done(self, ret):
        self._rst.configure(state="normal", text="重启 WSL")
        if ret is True:
            self._async_refresh()
        else:
            messagebox.showerror("错误", f"重启失败: {ret}")


# ─── Entry ───────────────────────────────────────────────────────────────────
def main():
    root = ctk.CTk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
