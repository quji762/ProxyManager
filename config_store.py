"""Config persistence, host validation, and HKCU autostart.

Lives next to app.py so PACKAGE_DIR resolves ui/ and assets/ both from
source and from a PyInstaller --onefile extract (sys._MEIPASS).
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import msvcrt
import os
import re
import shutil
import sys
import threading
import time

import proxy_manager as pm

# ── APP_VERSION: single source of truth ──────────────────────────────────────
# pyproject.toml reads this via [tool.setuptools.dynamic] version = attr, and
# the release workflow parses the same literal. Do not consult
# importlib.metadata here: a previously installed proxy-manager in
# site-packages would override this checkout (and a `pip install .` of this
# tree) with that stale version. Source runs and PyInstaller builds both
# execute this file, so the literal is the version either way.
APP_VERSION = "1.4.0"

# ── DPAPI helpers for password encryption ────────────────────────────────────
_DPAPI_PREFIX = "DPAPI:"


class DpapiError(Exception):
    """DPAPI 加密失败。调用方不得把明文密码写进 config.json。"""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Without argtypes, 64-bit pointers in DATA_BLOB are truncated the same way
# SetWindowPos(HWND) used to be. LocalFree must also see a full HANDLE.
_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p, ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD,
    ctypes.POINTER(_DATA_BLOB)]
_crypt32.CryptProtectData.restype = ctypes.wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(_DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD,
    ctypes.POINTER(_DATA_BLOB)]
_crypt32.CryptUnprotectData.restype = ctypes.wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _dpapi_encrypt(plaintext: str) -> str:
    """Encrypt a string with Windows DPAPI; returns base64 with DPAPI: prefix.

    失败时抛 DpapiError，绝不回退明文。调用方应拒绝保存并提示用户。
    """
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
        raise ctypes.WinError(ctypes.get_last_error())
    except DpapiError:
        raise
    except Exception as exc:
        pm.log_exception("dpapi_encrypt: DPAPI 加密失败，拒绝明文落盘")
        raise DpapiError("DPAPI 加密失败，密码未保存") from exc


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
        raise ctypes.WinError(ctypes.get_last_error())
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
# Named address presets and user config backups are bounded so a buggy or
# malicious caller cannot grow config.json / the backups folder without limit.
MAX_PRESETS = 24
MAX_PRESET_NAME = 40
MAX_BACKUPS = 30
PROBE_URLS = (
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://captive.apple.com/hotspot-detect.html",
    "http://connect.rom.miui.com/generate_204",
    "http://www.gstatic.com/generate_204",
)

_cfg_lock = threading.RLock()


class _cross_process_lock:
    """Cross-process advisory lock around the config read-merge-write.

    _cfg_lock is process-local: with PM_NO_SINGLETON (or a failed mutex) two
    instances can both read config.json, merge their own view over it and
    os.replace it — the loser silently drops every key the other wrote,
    including the restore stash. msvcrt.locking serializes the whole
    critical section across processes. Failing to take the lock degrades to
    the in-process lock only: an unusable lock file must never make saving
    impossible.
    """

    def __init__(self):
        self._fd = None

    def __enter__(self):
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            # Path resolved from APP_DIR at call time (not a module constant)
            # so tests can redirect it with the rest of the config paths.
            self._fd = os.open(os.path.join(APP_DIR, "config.json.lock"),
                               os.O_RDWR | os.O_CREAT, 0o600)
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_LOCK, 1)
        except Exception:
            pm.log_exception("config_lock: 跨进程锁不可用，降级为进程内锁")
            self._close()
        return self

    def __exit__(self, *exc):
        self._unlock()
        self._close()
        return False

    def _unlock(self):
        if self._fd is None:
            return
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

    def _close(self):
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.close(fd)
        except OSError:
            pass


# Set on the first bak/corrupt recovery this process sees; sticky so the UI
# can toast after Api.__init__ already consumed load_config().
_load_warning = None  # None | "bak" | "corrupt"

_SAVE_KEYS = (
    "host", "port", "scheme", "user", "password", "lang",
    "powershell", "wsl", "git_win", "git_wsl",
    "npm", "pip", "npmwsl", "pipwsl", "pin", "mode", "ports",
    "autostart", "bg_path", "bg_dim", "card_alpha", "speed_full",
    "quit_clear", "wsl_all_distros", "close_hint_done", "restore",
    "applied",
    "residual",
    "wsl_written",
    "presets",
    "schema_version",
    "win_rect", "win_max",
)


def valid_host(h) -> bool:
    return bool(h) and not any(c.isspace() for c in h) \
        and all(c in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in h) \
        and "://" not in h and ":" not in h


def _load_applied(raw):
    """Validate the persisted applied-* address, or None when never written.

    A missing key, a non-dict value, an empty dict, or an invalid host/port
    ALL mean one thing: this tool has never written an endpoint, and callers
    must treat the result as "no record". host/port are the IDENTITY fields —
    they answer "is this address ours?", which is what _ours_proxy_urls /
    _quit_clear_owns compare against — so a corrupt value yields no record at
    all rather than a record pointing somewhere else.

    Backfilling from the form fields (the old fallback) is exactly what
    contract A2 / the AGENTS.md core invariant forbids: a hand-edited or
    half-corrupt config.json would then produce a valid-looking record for an
    address this tool never wrote, and quit-clear would silently delete the
    user's own proxy at that same address.

    scheme/user/password only take part in URL equality, so a corrupt value
    there is sanitized to a safe default instead of dropping the whole record:
    the defaults can never make a user-configured proxy compare equal to ours.
    """
    if not isinstance(raw, dict):
        return None
    host = str(raw.get("host") or "")
    port = str(raw.get("port") or "")
    # host/port identify the record: an invalid value means there is no
    # usable record, never a repaired one.
    if not valid_host(host):
        return None
    if not pm.is_valid_port(port):
        return None
    port = str(int(port))
    scheme = str(raw.get("scheme") or "http").strip().lower()
    user = str(raw.get("user") or "")
    password = str(raw.get("password") or "")
    if password:
        password = _dpapi_decrypt(password)
    if not pm.is_valid_scheme(scheme):
        scheme = "http"
    if not pm.is_valid_userinfo(user):
        user = ""
    if not pm.is_valid_userinfo(password):
        password = ""
    return {"host": host, "port": port, "scheme": scheme,
            "user": user, "password": password}


# Every endpoint a residual record may name. Unknown keys are dropped so a
# corrupt config cannot inject arbitrary "targets" into the ownership checks.
_RESIDUAL_TARGETS = ("ps", "sys", "wsl", "gitwin", "gitwsl",
                     "npm", "pip", "npmwsl", "pipwsl")


def _load_residual(raw):
    """Sanitize persisted residual-ownership records.

    A residual record names an address this tool wrote to a target but that
    is NOT the applied record — only produced when a port-switch rollback
    fails and leaves the endpoint on the new address. It exists so quit-clear
    and disable still recognize that endpoint as ours instead of orphaning a
    proxy this tool turned on. Each entry is validated exactly like `applied`
    (invalid host/port → dropped), and unknown target keys are discarded.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for t in _RESIDUAL_TARGETS:
        rec = _load_applied(raw.get(t))
        if rec is not None:
            out[t] = rec
    return out


def _load_presets(raw):
    """Sanitize the persisted named address presets.

    Each preset is a saved FORM address (host/port/scheme/user/password), not
    an applied record — applying one goes through switch_port, which owns the
    applied/restore semantics. A non-list yields no presets; each entry must
    carry a usable name and a valid host/port identity (the same fields that
    make an address actionable), while scheme/user/password are sanitized to
    safe defaults. Passwords are DPAPI-decrypted here exactly like the main
    password field. Names are deduped (last wins) and the list is capped.
    """
    if not isinstance(raw, list):
        return []
    by_name = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or len(name) > MAX_PRESET_NAME:
            continue
        host = str(item.get("host") or "")
        port = str(item.get("port") or "")
        if not valid_host(host) or not pm.is_valid_port(port):
            continue
        scheme = str(item.get("scheme") or "http").strip().lower()
        if not pm.is_valid_scheme(scheme):
            scheme = "http"
        user = str(item.get("user") or "")
        if not pm.is_valid_userinfo(user):
            user = ""
        password = str(item.get("password") or "")
        if password:
            password = _dpapi_decrypt(password)
        if not pm.is_valid_userinfo(password):
            password = ""
        by_name[name] = {"name": name, "host": host, "port": str(int(port)),
                         "scheme": scheme, "user": user, "password": password}
    return list(by_name.values())[-MAX_PRESETS:]


def config_load_warning():
    """'bak' if this process recovered from config.json.bak, 'corrupt' if
    both the primary file and the bak were unreadable, else None."""
    return _load_warning


def _load_json_object(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return data


def _quarantine_config():
    """Move the unreadable primary aside so the next write starts clean.

    os.replace first (atomic); copy2 only as a fallback when the rename
    itself fails (locked file, cross-volume). Both are best-effort: the
    caller must keep working even if nothing could be quarantined.
    """
    try:
        if os.path.isfile(CONFIG_FILE):
            os.replace(CONFIG_FILE, CONFIG_FILE + ".corrupt")
            return
    except OSError:
        pass
    try:
        if os.path.isfile(CONFIG_FILE):
            shutil.copy2(CONFIG_FILE, CONFIG_FILE + ".corrupt")
    except OSError:
        pass


def _recover_from_bak():
    """Quarantine the unreadable primary file, then try config.json.bak.

    The restore stash lives in the JSON: swallowing a parse error as `{}`
    meant disable/quit-clear could no longer put the user's previous proxy
    back. A successful save (and the first successful load) keep a bak.
    """
    _quarantine_config()
    bak = CONFIG_FILE + ".bak"
    try:
        data = _load_json_object(bak)
    except FileNotFoundError:
        return {}, "corrupt"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pm.log_exception("load_config: .bak 也无法读取")
        return {}, "corrupt"
    try:
        shutil.copy2(bak, CONFIG_FILE)
    except OSError:
        pass
    return data, "bak"


# Bumped whenever a load-time migration changes the meaning of an on-disk
# key. A config without the key is treated as version 0 (written before this
# framework existed). Each migration step below must be idempotent — load runs
# on every start, including on configs that are already current.
CONFIG_SCHEMA_VERSION = 2


def _migrate_config(cfg):
    """Apply ordered, idempotent schema migrations to a raw on-disk config.

    Returns True when anything changed (or the version was stamped), so
    load_config can persist the upgraded file once. Keying off the version
    instead of re-inspecting every key each load keeps future format changes
    deterministic and greppable.
    """
    try:
        version = int(cfg.get("schema_version") or 0)
    except (TypeError, ValueError):
        version = 0
    changed = False
    # v1: the single legacy `git` switch split into git_win / git_wsl.
    if version < 1 and "git" in cfg and "git_win" not in cfg:
        cfg["git_win"] = cfg["git_wsl"] = bool(cfg["git"])
        changed = True
    # v2: the old 5 s "full speed" default became 40.
    if version < 2 and cfg.get("speed_full") == 5:
        cfg["speed_full"] = 40
        changed = True
    if version != CONFIG_SCHEMA_VERSION:
        cfg["schema_version"] = CONFIG_SCHEMA_VERSION
        changed = True
    return changed


def load_config():
    global _load_warning
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
    source = "missing"
    # Share the write-side lock so a concurrent save (tmp file + os.replace)
    # is never observed half-done.
    with _cfg_lock:
        try:
            cfg = _load_json_object(CONFIG_FILE)
            source = "file"
        except FileNotFoundError:
            cfg, source = {}, "missing"
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            pm.log_exception("load_config: 主配置损坏，将尝试 .bak")
            cfg, source = _recover_from_bak()
        if source == "file":
            bak = CONFIG_FILE + ".bak"
            if not os.path.isfile(bak):
                try:
                    shutil.copy2(CONFIG_FILE, bak)
                except OSError:
                    pass
    if source in ("bak", "corrupt") and _load_warning is None:
        _load_warning = source

    migrated = _migrate_config(cfg)

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
    # No fallback to the form address: a corrupt host/port yields None
    # ("never written"), never a repaired record (see _load_applied).
    out["applied"] = _load_applied(cfg.get("applied"))
    out["residual"] = _load_residual(cfg.get("residual"))
    out["presets"] = _load_presets(cfg.get("presets"))
    out["schema_version"] = CONFIG_SCHEMA_VERSION
    # Persist an upgrade only when a real file was read: a corrupt config was
    # just quarantined and a missing one never existed, so re-saving here would
    # silently recreate config.json and mask the load warning.
    if migrated and source in ("file", "bak"):
        try:
            save_config(out)
        except Exception:
            pass
    return out


def _read_current_config():
    """Current on-disk config for the save-time merge.

    A primary that cannot be parsed as a JSON object is quarantined as
    .corrupt (same isolation _recover_from_bak uses) instead of being
    swallowed as `{}`: merging into an empty dict used to write back a file
    holding only this save's keys, silently destroying every other key on
    disk — including the restore stash.
    """
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cur = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pm.log_exception("save_config: 主配置无法读取，隔离为 .corrupt 后重建")
        _quarantine_config()
        return {}
    if not isinstance(cur, dict):
        pm.log_exception("save_config: 主配置不是 JSON 对象，隔离为 .corrupt 后重建")
        _quarantine_config()
        return {}
    return cur


def _write_bak():
    """Refresh config.json.bak atomically.

    copy2 straight onto the .bak left a half-written file behind if the
    process died mid-copy, and _recover_from_bak can only fall back to
    defaults for a truncated bak. The tmp name carries the pid so two
    instances never share it. A failed bak refresh is not fatal — the
    primary save has already landed.
    """
    bak_tmp = f"{CONFIG_FILE}.{os.getpid()}.bak.tmp"
    try:
        shutil.copy2(CONFIG_FILE, bak_tmp)
        os.replace(bak_tmp, CONFIG_FILE + ".bak")
    except OSError:
        try:
            os.unlink(bak_tmp)
        except OSError:
            pass


def save_config(cfg):
    """Write the full superset; unknown keys from future versions survive.
    Returns True on success, False if the write failed."""
    # The cross-process lock spans the whole read-merge-write below, so two
    # instances cannot each merge their own view of the file and clobber the
    # other's keys. It stays INNERMOST: js_api.Api.save_config already holds
    # _cfg_lock when it calls in here.
    with _cfg_lock, _cross_process_lock():
        # Write to a temp file and os.replace it: open("w") would truncate
        # config.json first, so a crash mid-dump (disk full, power loss)
        # destroyed the file including the restore stash. os.replace is
        # atomic on the same volume.
        # Unique per-process tmp name: _cfg_lock is process-local, so two
        # instances (PM_NO_SINGLETON / failed mutex) must not share one tmp.
        tmp = f"{CONFIG_FILE}.{os.getpid()}.tmp"
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            cur = _read_current_config()
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
            if "residual" in save_data and isinstance(save_data["residual"], dict):
                enc_res = {}
                for t, r in save_data["residual"].items():
                    if not isinstance(r, dict):
                        continue
                    r = dict(r)
                    pw = r.get("password")
                    if pw and not str(pw).startswith(_DPAPI_PREFIX):
                        r["password"] = _dpapi_encrypt(str(pw))
                    enc_res[t] = r
                save_data["residual"] = enc_res
            if "presets" in save_data and isinstance(save_data["presets"], list):
                enc_presets = []
                for p in save_data["presets"]:
                    if not isinstance(p, dict):
                        continue
                    p = dict(p)
                    pw = p.get("password")
                    if pw and not str(pw).startswith(_DPAPI_PREFIX):
                        p["password"] = _dpapi_encrypt(str(pw))
                    enc_presets.append(p)
                save_data["presets"] = enc_presets
            cur.update(save_data)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_FILE)
            _write_bak()
            return True
        except DpapiError:
            raise
        except (OSError, TypeError, ValueError):
            # json.dump raises TypeError on an unserializable value and
            # ValueError on a circular reference; both used to escape to
            # pywebview with the tmp file left behind. Every write failure
            # leaves the previous config.json intact.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False


# ── user config backups ──────────────────────────────────────────────────────
# A backup is a verbatim snapshot of config.json, so the DPAPI-encrypted
# secrets stay encrypted — no plaintext is ever written to a backup file.
# Backups live in a managed folder (no arbitrary caller paths) and restore is
# confined to form fields + preferences by js_api, never the ownership records.
_BACKUP_NAME_RE = re.compile(r"^config-\d{8}-\d{6}(?:-\d+)?\.json$")


def backups_dir():
    """Folder holding user config backups. Resolved from APP_DIR at call time
    so tests can redirect it alongside CONFIG_FILE."""
    return os.path.join(APP_DIR, "backups")


def _safe_backup_path(name):
    """Resolve a caller-supplied backup name to a path INSIDE backups_dir, or
    None. The strict basename pattern rejects path separators and '..'
    outright; the realpath containment check is defense in depth."""
    name = os.path.basename(str(name or ""))
    if not _BACKUP_NAME_RE.match(name):
        return None
    directory = os.path.realpath(backups_dir())
    path = os.path.join(directory, name)
    try:
        if os.path.commonpath([os.path.realpath(path), directory]) != directory:
            return None
    except ValueError:
        return None
    return path


def _prune_backups(directory):
    entries = []
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for n in names:
        if not _BACKUP_NAME_RE.match(n):
            continue
        p = os.path.join(directory, n)
        try:
            entries.append((os.stat(p).st_mtime, p))
        except OSError:
            continue
    entries.sort()
    for _, p in entries[:max(0, len(entries) - MAX_BACKUPS)]:
        try:
            os.unlink(p)
        except OSError:
            pass


def create_backup():
    """Snapshot the current config.json into the backups folder, returning
    {name, mtime, size}. Atomic tmp + os.replace so a crash cannot leave a
    half-written backup; oldest backups beyond MAX_BACKUPS are pruned."""
    directory = backups_dir()
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"config-{stamp}.json"
    n = 1
    while os.path.exists(os.path.join(directory, name)):
        n += 1
        name = f"config-{stamp}-{n}.json"
    tmp = os.path.join(directory, f".{name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(CONFIG_FILE, tmp)
        final = os.path.join(directory, name)
        os.replace(tmp, final)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _prune_backups(directory)
    st = os.stat(final)
    return {"name": name, "mtime": st.st_mtime, "size": st.st_size}


def list_backups():
    """Available backups, newest first: [{name, mtime, size}]."""
    directory = backups_dir()
    out = []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    for n in names:
        if not _BACKUP_NAME_RE.match(n):
            continue
        try:
            st = os.stat(os.path.join(directory, n))
        except OSError:
            continue
        out.append({"name": n, "mtime": st.st_mtime, "size": st.st_size})
    out.sort(key=lambda b: b["mtime"], reverse=True)
    return out


def read_backup(name):
    """Parse a named backup as a JSON object, or None when missing/invalid.
    The name is validated and confined to the backups folder, so a caller
    cannot read an arbitrary file."""
    path = _safe_backup_path(name)
    if not path or not os.path.isfile(path):
        return None
    try:
        return _load_json_object(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pm.log_exception("read_backup: 备份无法读取")
        return None


def delete_backup(name):
    path = _safe_backup_path(name)
    if not path or not os.path.isfile(path):
        return False
    try:
        os.unlink(path)
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
