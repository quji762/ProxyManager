"""Config persistence, host validation, and HKCU autostart.

Lives next to app.py so package_dir() resolves ui/ and assets/ both from
source and from a PyInstaller --onefile extract (sys._MEIPASS).
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading

import proxy_manager as pm

# ── APP_VERSION: single source of truth from package metadata ────────────────
try:
    from importlib.metadata import version as _pkg_version
    APP_VERSION = _pkg_version("proxy-manager")
except Exception:
    APP_VERSION = "1.4.0"

# ── DPAPI helpers for password encryption ────────────────────────────────────
_DPAPI_PREFIX = "DPAPI:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32")
_kernel32 = ctypes.WinDLL("kernel32")


def _dpapi_encrypt(plaintext: str) -> str:
    """Encrypt a string with Windows DPAPI; returns base64 with DPAPI: prefix.
    Falls back to returning the plaintext on failure (best-effort, logged)."""
    try:
        data = plaintext.encode("utf-8")
        blob_in = _DATA_BLOB(
            len(data),
            ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                        ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        if _crypt32.CryptProtectData(
                ctypes.byref(blob_in), None, None, None, None, 0,
                ctypes.byref(blob_out)):
            encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            _kernel32.LocalFree(blob_out.pbData)
            return _DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")
        # WinAPI returned FALSE (e.g. DPAPI unavailable): raise so the
        # fallback below is logged too — never silently store plaintext.
        raise ctypes.WinError(ctypes.GetLastError())
    except Exception:
        pm.log_exception("dpapi_encrypt: DPAPI 加密失败，已回退明文存储")
    return plaintext  # fallback: store as-is


def _dpapi_decrypt(stored: str) -> str:
    """Decrypt a DPAPI-encrypted value (identified by DPAPI: prefix).
    Returns the plaintext, or the original string if it was not encrypted."""
    if not stored or not stored.startswith(_DPAPI_PREFIX):
        return stored  # legacy plaintext
    try:
        raw = base64.b64decode(stored[len(_DPAPI_PREFIX):])
        blob_in = _DATA_BLOB(
            len(raw),
            ctypes.cast(ctypes.create_string_buffer(raw, len(raw)),
                        ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        if _crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0,
                ctypes.byref(blob_out)):
            decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            _kernel32.LocalFree(blob_out.pbData)
            return decrypted.decode("utf-8")
        # WinAPI returned FALSE: log and fall through to the plaintext
        # compatibility path (behavior unchanged).
        raise ctypes.WinError(ctypes.GetLastError())
    except Exception:
        pm.log_exception("dpapi_decrypt: DPAPI 解密失败，已按原样返回")
    return stored  # fallback: treat as plaintext

if getattr(sys, "frozen", False):
    PACKAGE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

ENTRY_SCRIPT = os.path.join(PACKAGE_DIR, "app.py")
UI_PATH = os.path.join(PACKAGE_DIR, "ui", "index.html")
TRAY_ICON = os.path.join(PACKAGE_DIR, "assets", "tray.png")

APP_DIR = pm.config_dir()
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
AUTOSTART_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "ProxyManager"

DEFAULT_PORTS = [7890, 7892, 7897]
MAX_BG_BYTES = 8 * 1024 * 1024
PROBE_URLS = (
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://captive.apple.com/hotspot-detect.html",
    "http://connect.rom.miui.com/generate_204",
    "http://www.gstatic.com/generate_204",
)

_cfg_lock = threading.RLock()

_SAVE_KEYS = (
    "host", "port", "scheme", "user", "password", "lang",
    "powershell", "wsl", "git_win", "git_wsl",
    "npm", "pip", "npmwsl", "pipwsl", "pin", "mode", "ports",
    "autostart", "bg_path", "bg_dim", "card_alpha", "speed_full",
    "quit_clear", "wsl_all_distros", "close_hint_done", "restore",
    "applied",
    "wsl_written",
    "win_rect", "win_max",
)


def package_dir() -> str:
    return PACKAGE_DIR


def valid_host(h) -> bool:
    return bool(h) and not any(c.isspace() for c in h) \
        and all(c in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in h) \
        and "://" not in h and ":" not in h


def _load_applied(raw, fallback):
    """Validate the persisted applied-* address, or copy the form fields.

    Legacy configs have no `applied` key; treating the form fields as
    applied matches the previous startup behavior (no worse than today).
    """
    if not isinstance(raw, dict):
        return dict(fallback)
    host = str(raw.get("host") or "")
    port = str(raw.get("port") or "")
    scheme = str(raw.get("scheme") or "http").strip().lower()
    user = str(raw.get("user") or "")
    password = str(raw.get("password") or "")
    if password:
        password = _dpapi_decrypt(password)
    if not valid_host(host):
        host = fallback["host"]
    if pm.is_valid_port(port):
        port = str(int(port))
    else:
        port = fallback["port"]
    if not pm.is_valid_scheme(scheme):
        scheme = "http"
    if not pm.is_valid_userinfo(user):
        user = ""
    if not pm.is_valid_userinfo(password):
        password = ""
    return {"host": host, "port": port, "scheme": scheme,
            "user": user, "password": password}


def load_config():
    defaults = {
        "host": pm.DEFAULT_HOST, "port": pm.DEFAULT_PORT,
        "scheme": "http", "user": "", "password": "", "lang": "zh",
        "powershell": True, "wsl": True, "git_win": False, "git_wsl": False,
        "npm": False, "pip": False, "npmwsl": False, "pipwsl": False,
        "pin": False, "mode": "light", "ports": list(DEFAULT_PORTS),
        "autostart": False, "bg_path": "", "bg_dim": 0.45, "card_alpha": 0.92,
        "quit_clear": False, "wsl_all_distros": True, "close_hint_done": False,
        "restore": {},
    }
    cfg = {}
    # Share the write-side lock so a concurrent save (tmp file + os.replace)
    # is never observed half-done.
    with _cfg_lock:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                cfg = {}
        except Exception:
            cfg = {}

    if "git" in cfg and "git_win" not in cfg:
        cfg["git_win"] = cfg["git_wsl"] = bool(cfg["git"])

    out = dict(defaults)
    for k in ("host", "port", "mode", "bg_path", "scheme", "user", "password", "lang"):
        if cfg.get(k) is not None:
            out[k] = str(cfg[k])
    if not valid_host(out["host"]):
        out["host"] = defaults["host"]
    if pm.is_valid_port(out["port"]):
        out["port"] = str(int(out["port"]))
    else:
        out["port"] = defaults["port"]
    if not pm.is_valid_scheme(out["scheme"]):
        out["scheme"] = "http"
    else:
        out["scheme"] = out["scheme"].strip().lower()
    if not pm.is_valid_userinfo(out["user"]):
        out["user"] = ""
    # Decrypt DPAPI-encrypted password (legacy plaintext passes through)
    if out["password"]:
        out["password"] = _dpapi_decrypt(out["password"])
    if not pm.is_valid_userinfo(out["password"]):
        out["password"] = ""
    if out["lang"] not in ("zh", "en"):
        out["lang"] = "zh"
    for k in ("powershell", "wsl", "git_win", "git_wsl", "npm", "pip",
              "npmwsl", "pipwsl", "pin", "autostart", "quit_clear",
              "wsl_all_distros", "close_hint_done"):
        out[k] = bool(cfg.get(k, defaults[k]))
    if out["mode"] not in ("light", "dark"):
        out["mode"] = "light"

    ports = cfg.get("ports")
    if isinstance(ports, list):
        # 键存在（即使显式 []）就按清洗结果采纳：`if seen:` 会把用户删光的
        # 预设静默复活成默认端口。键缺失时才回落 defaults。
        seen = []
        for p in ports:
            try:
                pi = int(p)
            except (TypeError, ValueError):
                continue
            if 1 <= pi <= 65535 and pi not in seen:
                seen.append(pi)
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
    try:
        migrated = False
        if cfg.get("speed_full") == 5:
            cfg["speed_full"] = 40
            migrated = True
        s = float(cfg.get("speed_full", 40))
        out["speed_full"] = round(s, 1) if 0 < s <= 100000 else 40
    except (TypeError, ValueError):
        out["speed_full"] = 40
    rect = cfg.get("win_rect")
    out["win_rect"] = None
    if isinstance(rect, list) and len(rect) == 4:
        try:
            out["win_rect"] = [int(v) for v in rect]
        except (TypeError, ValueError):
            pass
    out["win_max"] = bool(cfg.get("win_max", False))
    restore = cfg.get("restore")
    out["restore"] = restore if isinstance(restore, dict) else {}
    # Last URL actually written to each WSL-side endpoint (raw/encrypted form;
    # js_api decrypts via _stash_decrypt, same pattern as the restore stash).
    wsl_written = cfg.get("wsl_written")
    out["wsl_written"] = wsl_written if isinstance(wsl_written, dict) else {}
    # Last address actually written to endpoints. Distinct from the form
    # fields (host/port/…) which persistSoon may save before switch_port.
    out["applied"] = _load_applied(cfg.get("applied"), {
        "host": out["host"], "port": out["port"], "scheme": out["scheme"],
        "user": out["user"], "password": out["password"],
    })
    if migrated:
        try:
            save_config(out)
        except Exception:
            pass
    return out


def save_config(cfg):
    """Write the full superset; unknown keys from future versions survive.
    Returns True on success, False if the write failed."""
    with _cfg_lock:
        # Write to a temp file and os.replace it: open("w") would truncate
        # config.json first, so a crash mid-dump (disk full, power loss)
        # destroyed the file including the restore stash. os.replace is
        # atomic on the same volume.
        tmp = CONFIG_FILE + ".tmp"
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
            save_data = {k: cfg[k] for k in _SAVE_KEYS if k in cfg}
            # Encrypt password with DPAPI before persisting
            if "password" in save_data and save_data["password"]:
                save_data["password"] = _dpapi_encrypt(save_data["password"])
            if "applied" in save_data and isinstance(save_data["applied"], dict):
                ap = dict(save_data["applied"])
                pw = ap.get("password")
                if pw and not str(pw).startswith(_DPAPI_PREFIX):
                    ap["password"] = _dpapi_encrypt(str(pw))
                save_data["applied"] = ap
            cur.update(save_data)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_FILE)
            return True
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    return f'"{sys.executable}" "{ENTRY_SCRIPT}" --tray'


def autostart_enabled():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN, 0,
                             winreg.KEY_READ)
        try:
            try:
                v, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            except FileNotFoundError:
                return False
            val = str(v)
            return str(autostart_command()) in val or (
                getattr(sys, "frozen", False) and sys.executable in val) or (
                ENTRY_SCRIPT in val)
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def autostart_set(on):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN, 0,
                             winreg.KEY_SET_VALUE)
        try:
            if on:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                                  autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return True
    except OSError as e:
        return str(e)
