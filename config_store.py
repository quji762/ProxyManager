"""Config persistence, host validation, and HKCU autostart.

Lives next to app.py so package_dir() resolves ui/ and assets/ both from
source and from a PyInstaller --onefile extract (sys._MEIPASS).
"""

from __future__ import annotations

import json
import os
import sys
import threading

import proxy_manager as pm

APP_VERSION = "1.4.0"

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
    "win_rect", "win_max",
)


def package_dir() -> str:
    return PACKAGE_DIR


def valid_host(h) -> bool:
    return bool(h) and not any(c.isspace() for c in h) \
        and all(c in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in h) \
        and "://" not in h and ":" not in h


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
            cur.update({k: cfg[k] for k in _SAVE_KEYS if k in cfg})
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2, ensure_ascii=False)
            return True
        except OSError:
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
