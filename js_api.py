"""JS bridge (pywebview js_api) between the UI and proxy_manager."""

import ctypes
import os
import subprocess
import threading
import time

import proxy_manager as pm
from config_store import (
    APP_VERSION, DEFAULT_PORTS, MAX_BG_BYTES, PROBE_URLS, _cfg_lock,
    _DPAPI_PREFIX, _dpapi_decrypt, _dpapi_encrypt,
    autostart_enabled, autostart_set, load_config, save_config, valid_host,
)
from window_win32 import (
    _POINT, _RECT, _is_maximized, _our_hwnds, set_resize_maximized, user32,
)

# File picker filter: must stay in sync with the extensions save_config
# accepts and get_background can serve.
_BG_FILTER = "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif"

# 恢复暂存中的凭据保护：ps 的 env 值与其余端点的完整 URL 可能携带
# user:pass@ 明文凭据，落盘 config.json 前逐值 DPAPI 加密（与 password
# 字段同机制）；读回时按 DPAPI: 前缀解密，旧版明文暂存原样兼容。
_STASH_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def _stash_encrypt(restore):
    out = {}
    for target, payload in (restore or {}).items():
        if isinstance(payload, str):
            # 裸 URL 字符串暂存（npm/pip/gitwin/gitwsl）同样可能带
            # user:pass@ 明文凭据；已加密值保持原样避免双重加密。
            out[target] = (_dpapi_encrypt(payload)
                           if payload and not str(payload).startswith(_DPAPI_PREFIX)
                           else payload)
            continue
        if not isinstance(payload, dict):
            out[target] = payload
            continue
        p = dict(payload)
        env = p.get("env")
        if isinstance(env, dict):
            enc_env = {}
            for k, v in env.items():
                if k not in _STASH_ENV_KEYS:
                    continue
                if not v:
                    enc_env[k] = None
                elif str(v).startswith(_DPAPI_PREFIX):
                    enc_env[k] = v          # 已加密：幂等原样
                else:
                    enc_env[k] = _dpapi_encrypt(v)
            p["env"] = enc_env
        url = p.get("url")
        if url and not str(url).startswith(_DPAPI_PREFIX):
            p["url"] = _dpapi_encrypt(str(url))
        out[target] = p
    return out


def _stash_decrypt(restore):
    out = {}
    for target, payload in (restore or {}).items():
        if isinstance(payload, str):
            # _dpapi_decrypt 自带 DPAPI: 前缀判断，旧版明文原样返回。
            out[target] = _dpapi_decrypt(payload) if payload else payload
            continue
        if not isinstance(payload, dict):
            out[target] = payload
            continue
        p = dict(payload)
        env = p.get("env")
        if isinstance(env, dict):
            p["env"] = {
                k: (_dpapi_decrypt(v) if v else None)
                for k, v in env.items()
            }
        url = p.get("url")
        if url and str(url).startswith(_DPAPI_PREFIX):
            p["url"] = _dpapi_decrypt(str(url))
        out[target] = p
    return out


class Api:
    """JS bridge. Every method returns JSON-serializable data and runs on its
    own thread (pywebview dispatches js_api calls off the UI thread), so the
    slow WSL subprocess calls never block rendering."""

    def __init__(self):
        cfg = load_config()
        self.host = cfg["host"]
        self.port = cfg["port"]
        self.scheme = cfg.get("scheme", "http")
        self.user = cfg.get("user", "")
        self.password = cfg.get("password", "")
        # Address this tool actually APPLIED to endpoints, kept current by
        # switch_port and do_toggle enable. save_config only mirrors the form
        # fields into self.host/self.port, so a typed-but-not-yet-applied
        # address must not make switch_port treat the old endpoints as
        # foreign ones (it compares against applied_*).
        self.applied_host = cfg["host"]
        self.applied_port = cfg["port"]
        self.applied_kw = {"scheme": self.scheme, "user": self.user,
                           "password": self.password}
        self._speed_lock = threading.Lock()
        # Serializes endpoint mutations (toggles, port switch, tray-quit
        # clear on the pystray thread): _restore / applied_* / _git_managed
        # are only consistent when read and written inside one critical
        # section. Must stay the OUTERMOST lock — _speed_lock and _cfg_lock
        # are acquired inside it, never the reverse. RLock because do_toggle
        # re-enters itself through _restore_previous.
        self._endpoint_mutation_lock = threading.RLock()
        self._speed_prev = pm.get_net_counters()
        self._speed = {"up": "0 B/s", "dn": "0 B/s", "dn_num": 0.0,
                       "iface": pm._primary_iface or "", "alive": None,
                       "port": ""}
        self._window = None
        self._icon = None
        self._pinned = cfg.get("pin", False)
        self._maximized = False
        self._prev_bounds = None
        self._bg_cache = None  # (path, data_url)
        self._win_rect = cfg.get("win_rect")      # last normal bounds (physical px)
        self._win_max = bool(cfg.get("win_max", False))
        # Last URL this tool wrote to each git endpoint, so a disable after
        # the user edited host/port still unsets the old value.
        self._git_managed = {"gitwin": None, "gitwsl": None}
        self._tray_ok = False  # close falls back to quit when no tray exists
        self._restore = _stash_decrypt(cfg.get("restore") or {})
        pm.set_wsl_apply_all(cfg.get("wsl_all_distros", True))
        threading.Thread(target=self._speed_loop, daemon=True).start()

    # ── config ──
    def get_config(self):
        cfg = load_config()
        return {
            "version": APP_VERSION,
            "host": self.host, "port": self.port,
            "scheme": self.scheme, "user": self.user, "password": self.password,
            "lang": cfg.get("lang", "zh"),
            "mode": cfg.get("mode", "light"), "pin": cfg.get("pin", False),
            "ports": cfg.get("ports", DEFAULT_PORTS),
            "autostart": autostart_enabled(),
            "bg_path": cfg.get("bg_path", ""),
            "bg_dim": cfg.get("bg_dim", 0.45),
            "card_alpha": cfg.get("card_alpha", 0.92),
            "speed_full": cfg.get("speed_full", 40),
            "quit_clear": bool(cfg.get("quit_clear", False)),
            "wsl_all_distros": bool(cfg.get("wsl_all_distros", True)),
            "close_hint_done": bool(cfg.get("close_hint_done", False)),
        }

    def save_config(self, data):
        # "以内存态为准"的 restore 合并写，先做快照再进 _cfg_lock：
        # - 不能在持 _cfg_lock 时再拿 _endpoint_mutation_lock——既有锁序是
        #   mutation → cfg（do_toggle/_switch_port 持 mutation 时经
        #   _persist_restore 拿 cfg），反向构成 AB-BA 死锁；
        # - 也不能直接遍历 self._restore——toggle 路径持 mutation 锁插入
        #   新 key 后等 cfg 锁落盘，加密遍历中途遇字典变更会抛 RuntimeError，
        #   异常被前端 persistSoon 的 .catch 静默吞掉、保存丢失。
        # dict(d) 是单条 C 级拷贝（CPython 下持锁原子完成），快照后加密只
        # 碰私有副本；快照之后并发插入的 key 由 toggle 自己的
        # _persist_restore 落盘，合并语义不变。
        with self._endpoint_mutation_lock:
            restore_snapshot = dict(self._restore)
        with _cfg_lock:
            cfg = load_config()
            # Validate host/port server-side: they end up inside wsl bash -c
            # and git commands downstream, and the UI only validates the
            # enable path.
            # host/port 与网速探测线程共享：变更成对持锁提交，避免撕裂的
            # (host, port) 组合被拿去做一次指向错误地址的 TCP 探测
            with self._speed_lock:
                if "host" in data:
                    h = str(data["host"]).strip()
                    if valid_host(h):
                        self.host = h
                if "port" in data:
                    p = str(data["port"]).strip()
                    if pm.is_valid_port(p):
                        self.port = str(int(p))
            if "scheme" in data and pm.is_valid_scheme(data["scheme"]):
                self.scheme = str(data["scheme"]).strip().lower()
            if "user" in data and pm.is_valid_userinfo(data["user"]):
                self.user = str(data["user"])
            if "password" in data and pm.is_valid_userinfo(data["password"]):
                self.password = str(data["password"])
            if "lang" in data and str(data["lang"]) in ("zh", "en"):
                cfg["lang"] = str(data["lang"])
            cfg["host"], cfg["port"] = self.host, self.port
            cfg["scheme"], cfg["user"], cfg["password"] = (
                self.scheme, self.user, self.password)
            for k in ("powershell", "wsl", "git_win", "git_wsl", "npm", "pip",
                      "npmwsl", "pipwsl", "pin"):
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
                bp = str(data["bg_path"])
                ext = os.path.splitext(bp)[1].lower()
                if not bp:
                    cfg["bg_path"] = ""
                elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif") \
                        and os.path.isfile(bp):
                    cfg["bg_path"] = bp
                # else: keep the previous value instead of persisting a
                # bogus/missing path that get_background would choke on.
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
            if "speed_full" in data:
                try:
                    v = float(data["speed_full"])
                    if 0 < v <= 100000:
                        cfg["speed_full"] = round(v, 1)
                except (TypeError, ValueError):
                    pass
            if "quit_clear" in data:
                cfg["quit_clear"] = bool(data["quit_clear"])
            if "wsl_all_distros" in data:
                cfg["wsl_all_distros"] = bool(data["wsl_all_distros"])
                pm.set_wsl_apply_all(cfg["wsl_all_distros"])
            if "close_hint_done" in data:
                cfg["close_hint_done"] = bool(data["close_hint_done"])
            # restore 暂存属内部状态（含用户原代理 URL，可能带明文凭据）：
            # 只由 _stash_previous/_persist_restore 维护，不接受前端覆写
            if "win_rect" in data and isinstance(data["win_rect"], list) \
                    and len(data["win_rect"]) == 4:
                try:
                    rect = [int(v) for v in data["win_rect"]]
                    cfg["win_rect"] = rect
                    self._win_rect = rect
                except (TypeError, ValueError):
                    pass
            if "win_max" in data:
                cfg["win_max"] = bool(data["win_max"])
                self._win_max = bool(data["win_max"])
            # "以内存态为准"的合并语义必须保留（_persist_restore 与前端保存
            # 并发时丢暂存），但落盘前必须 DPAPI 加密：self._restore 是内存
            # 中的解密态明文，直接回写会把 bf9602c 的暂存加密撤销掉。
            # _stash_encrypt 对已带 DPAPI: 前缀的值原样保留，幂等。
            # 加密的是函数入口处的快照（见 save_config 开头注释）。
            cfg["restore"] = _stash_encrypt(restore_snapshot)
            if not save_config(cfg):
                return False
        return True

    # ── status refresh: one call per slice, fired in parallel by the UI ──
    def refresh_item(self, which):
        if which == "ps":
            return {"status": pm.get_ps_status(),
                    "vars": pm.get_ps_proxy_vars()}
        if which == "sys":
            return pm.get_sys_proxy()
        if which == "wsl":
            ip, block, wgit = pm.get_wsl_ip_and_block()
            return {"ip": ip, "on": bool(pm.get_wsl_status(block)),
                    "vars": pm.get_wsl_proxy_vars(block), "git": wgit}
        if which == "gitwin":
            return pm.get_git_proxy_win()
        if which == "npm":
            return pm.get_npm_proxy()
        if which == "pip":
            return pm.get_pip_proxy()
        if which == "npmwsl":
            return pm.get_npm_proxy_wsl()
        if which == "pipwsl":
            return pm.get_pip_proxy_wsl()
        if which == "ip":
            return pm.get_local_ip()
        if which == "mode":
            return pm.get_wsl_network_mode()
        return None

    # ── toggle flow: JS orchestrates confirm dialogs, Python applies ──
    def _proxy_kw(self, scheme=None, user=None, password=None):
        return {
            "scheme": scheme if scheme is not None else self.scheme,
            "user": user if user is not None else self.user,
            "password": password if password is not None else self.password,
        }

    @staticmethod
    def _sys_result(ret):
        """set_sys_proxy returns True, an error string, or (True, note) when
        a PAC (AutoConfigURL) is registered. Normalize to (ok, note)."""
        if isinstance(ret, tuple) and len(ret) == 2 and ret[0] is True:
            return True, ret[1]
        return ret is True, None

    def _detected_hpk(self, cur, fallback_h, fallback_p, fallback_kw):
        """Parse a detected proxy URL into (host, port, kw) or return fallbacks."""
        parsed = self._adopt_parsed(cur)
        if not parsed:
            return fallback_h, fallback_p, fallback_kw
        return parsed["host"], parsed["port"], {
            "scheme": parsed["scheme"], "user": parsed["user"],
            "password": parsed["password"]}

    # Dispatch table for simple toggle endpoints:
    # (setter name, label, getter name, on_note). Names are resolved through
    # getattr(pm, ...) at call time so tests can patch js_api.pm.set_* —
    # storing the function objects themselves would bind them at class
    # creation and silently bypass such patches (writing the real registry).
    # Git and WSL endpoints are handled separately due to their unique logic.
    _ENDPOINT_INFO = {
        "ps":     ("set_ps_proxy",      "PowerShell",  "get_ps_status",     "请新开一个终端后生效"),
        "sys":    ("set_sys_proxy",     "系统代理",    "get_sys_proxy",     None),
        "npm":    ("set_npm_proxy",     "npm",         "get_npm_proxy",     None),
        "pip":    ("set_pip_proxy",     "pip",         "get_pip_proxy",     None),
        "npmwsl": ("set_npm_proxy_wsl", "npm(WSL)",    "get_npm_proxy_wsl", None),
        "pipwsl": ("set_pip_proxy_wsl", "pip(WSL)",    "get_pip_proxy_wsl", None),
    }

    def _managed_url(self, host=None, port=None, **kw):
        opts = self._proxy_kw(**{k: kw[k] for k in ("scheme", "user", "password") if k in kw})
        return pm.build_proxy_url(
            host if host is not None else self.host,
            port if port is not None else self.port,
            **opts)

    def _adopt_parsed(self, cur):
        """Return parse_proxy_url dict or None; treats UNSET sentinels as empty."""
        if not cur or cur in (pm.UNSET, pm.UNDETECTED):
            return None
        return pm.parse_proxy_url(str(cur).strip())

    def _endpoint_addr(self, host, port):
        """Validated (host, port): explicit JS-passed values win over the
        cached self.host/self.port, so a toggle right after editing the
        fields uses the NEW address (persistSoon only debounces at 400ms)."""
        h = str(host).strip() if host else self.host
        p = str(port).strip() if port else self.port
        if not valid_host(h) or not pm.is_valid_port(p):
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
            return {"error": "代理地址或端口不合法，请先修正", "error_key": "errBadAddr"}
        host, port = addr
        if not pm.proxy_port_alive(host, port):
            return {"confirm": "port", "host": host, "port": str(port),
                    "message": f"{host}:{port} 上没有程序在监听（代理软件可能没有运行）。\n"
                               "继续启用会导致终端联网失败。\n仍要继续吗？"}
        if target in ("wsl", "gitwsl", "npmwsl", "pipwsl", "all") \
                and not pm.wsl_available():
            # Fail before any write attempt: a broken WSL would otherwise sit
            # through the full 15s subprocess timeout inside set_wsl_proxy.
            return {"error": "未检测到可用的 WSL 发行版", "error_key": "errNoWsl"}
        if target in ("wsl", "all"):
            shell = pm.wsl_default_shell()
            if shell and shell not in ("bash", "zsh", "fish", "sh"):
                return {"confirm": "shell", "shell": shell,
                        "message": f"WSL 默认 shell 是 {shell}，本工具写入 ~/.bashrc、"
                                   "~/.zshrc 和 fish conf.d。\n仍要继续写入吗？"}
        if target in ("gitwin", "all"):
            import shutil
            if not shutil.which("git"):
                return {"error": "未找到 git，请确认已安装 Git", "error_key": "errNoGit"}
        if target == "npm":
            # The config-file write itself never needs the npm CLI, but if
            # Node.js isn't even on PATH the endpoint is probably pointless —
            # ask instead of failing (pip is skipped: `py -m pip` etc. can't
            # be detected reliably and the file is always honored).
            import shutil
            if not shutil.which("npm"):
                return {"confirm": "npm",
                        "message": "PATH 上未检测到 npm（Node.js）。\n"
                                   "仍要写入 ~/.npmrc 代理配置吗？"}
        return {"ok": True}

    def _persist_restore(self):
        with _cfg_lock:
            cfg = load_config()
            # 暂存值 DPAPI 加密后落盘（含用户原代理凭据，不应明文持久化）
            cfg["restore"] = _stash_encrypt(self._restore)
            save_config(cfg)

    def _detected_url(self, target):
        if target == "ps":
            v = pm.get_ps_status()
        elif target == "sys":
            v = pm.get_sys_proxy()
        elif target == "gitwin":
            v = pm.get_git_proxy_win()
        elif target == "gitwsl":
            v = (self.refresh_item("wsl") or {}).get("git")
        elif target == "npm":
            v = pm.get_npm_proxy()
        elif target == "pip":
            v = pm.get_pip_proxy()
        elif target == "npmwsl":
            v = pm.get_npm_proxy_wsl()
        elif target == "pipwsl":
            v = pm.get_pip_proxy_wsl()
        else:
            return None
        parsed = self._adopt_parsed(v)
        return (parsed and str(v).strip()) or None

    def _stash_previous(self, target, new_url):
        """Remember a pre-existing proxy so disable can put it back."""
        if target == "wsl" or self._restore.get(target):
            return
        if target == "ps":
            # PowerShell enable overwrites HTTP/HTTPS/ALL_PROXY and merges
            # NO_PROXY — a single URL cannot round-trip a state where those
            # differed, so stash every original value (None = was unset).
            # Values equal to the URL this tool last applied are leftovers
            # from a previous enable (enable → edit port → enable again),
            # not user state: record None so the restore deletes them instead
            # of re-arming a proxy the user is turning off.
            ours = pm.build_proxy_url(self.applied_host, self.applied_port,
                                      **self.applied_kw)
            vars_ = pm.get_ps_proxy_vars()
            env = {n: (None if v in (pm.UNSET, "") or str(v) == ours else str(v))
                   for n, v in vars_.items()}
            no_proxy = pm.get_ps_no_proxy()
            if any(str(v) == ours for v in vars_.values()):
                # Our last enable also merged our entries into NO_PROXY —
                # stash only the user's own entries (none left → was unset).
                no_proxy = pm._stripped_no_proxy(no_proxy)
            env["NO_PROXY"] = no_proxy or None
            if not any(env.values()):
                return  # user had nothing here; the surgical disable suffices
            self._restore[target] = {"env": env}
            self._persist_restore()
            return
        if target == "sys":
            # System enable merges our bypass entries into ProxyOverride —
            # stash the full original list so disable puts it back verbatim
            # instead of subtracting a fixed set (which would also drop
            # entries the user had configured themselves).
            override = pm.get_sys_proxy_override()
            cur = self._detected_url(target)
            # A detected URL equal to what this tool last applied is our own
            # write (enable → edit port / re-enable), so the current
            # ProxyOverride merge is ours too: keep only the user's entries
            # and don't re-apply the old address on disable.
            if cur and cur == pm.wininet_url(self.applied_host,
                                             self.applied_port,
                                             self.applied_kw["scheme"]):
                cur = None
                override = ";".join(
                    e.strip() for e in override.split(";")
                    if e.strip() and e.strip() not in pm._PROXY_OVERRIDE_OURS)
            url = cur if (cur and cur != new_url) else None
            if not override and not url:
                return
            self._restore[target] = {"url": url, "override": override or None}
            self._persist_restore()
            return
        cur = self._detected_url(target)
        if cur and cur != new_url:
            self._restore[target] = cur
            self._persist_restore()

    def _restore_stashed(self, target, prev):
        """Restore a dict-shaped stash (ps env vars / sys bypass list): write
        the exact pre-enable values back instead of re-enabling through the
        single-URL enable path. Returns None for unhandled targets."""
        if target == "ps":
            env = prev.get("env")
            if not env:
                # A dict stash always carries a non-empty env (an all-empty
                # one is never stashed), so a missing/empty one is corrupted
                # data — keep the stash instead of "restoring" nothing, which
                # would silently drop the user's original values.
                return {"error": "暂存数据不完整"}
            ret = pm.set_ps_env_vars(env)
            if ret is not True:
                return {"error": f"PowerShell: {ret}"}
            return {"ok": True, "note": None}
        if target == "sys":
            pac_note = None
            parsed = self._adopt_parsed(prev.get("url"))
            if parsed:
                ret = pm.set_sys_proxy(
                    parsed["host"], parsed["port"], True,
                    scheme=parsed["scheme"], user=parsed["user"],
                    password=parsed["password"])
                ok, pac_note = self._sys_result(ret)
                if not ok:
                    return {"error": f"系统代理: {ret}"}
            else:
                # Only the bypass list was the user's: run the normal
                # surgical disable first, then put the list back.
                r = self.do_toggle("sys", False, _restoring=True)
                if not isinstance(r, dict) or not r.get("ok"):
                    return r if isinstance(r, dict) else {"error": "系统代理: 关闭失败"}
            ret = pm.set_sys_proxy_override(prev.get("override") or "")
            if ret is not True:
                return {"error": f"系统代理: {ret}"}
            # 恢复路径重新启用了用户原代理；若用户机器上有 PAC，把警示带出
            # （_restore_previous_locked 会把它拼进最终 note）。
            return {"ok": True, "note": pac_note}
        return None

    def _restore_previous(self, target):
        # Runs inside do_toggle's critical section; the RLock re-entry here
        # keeps a direct future caller equally serialized.
        with self._endpoint_mutation_lock:
            return self._restore_previous_locked(target)

    def _restore_previous_locked(self, target):
        prev = self._restore.get(target)
        if isinstance(prev, dict):
            r = self._restore_stashed(target, prev)
        else:
            parsed = self._adopt_parsed(prev)
            if not parsed:
                return None
            r = self.do_toggle(
                target, True, parsed["host"], parsed["port"],
                parsed["scheme"], parsed["user"], parsed["password"], True)
        if isinstance(r, dict) and r.get("ok"):
            self._restore.pop(target, None)
            self._persist_restore()
            extra = "已恢复覆盖前的代理"
            r["note"] = (r["note"] + "；" + extra) if r.get("note") else extra
            r["restored"] = True
            return r
        return r if isinstance(r, dict) else {"error": "恢复覆盖前的代理失败"}

    def clear_all_endpoints(self):
        """Disable every endpoint (used by 退出时关闭代理). Restores stashed
        values when we had overwritten them. Holds the mutation lock for the
        WHOLE sequence: a concurrent UI toggle must not interleave with the
        quit path and leave half-cleared state."""
        with self._endpoint_mutation_lock:
            for t in ("ps", "sys", "wsl", "gitwin", "gitwsl", "npm", "pip",
                      "npmwsl", "pipwsl"):
                try:
                    self.do_toggle(t, False)
                except Exception:
                    pm.log_exception(f"clear_all_endpoints:{t}")
            return True

    def do_toggle(self, target, on, host=None, port=None,
                  scheme=None, user=None, password=None, _restoring=False):
        """Serialize against the tray-quit clear (clear_all_endpoints runs
        on the pystray thread while js_api workers toggle in parallel)."""
        with self._endpoint_mutation_lock:
            return self._do_toggle(target, on, host, port, scheme, user,
                                   password, _restoring)

    def _do_toggle(self, target, on, host=None, port=None,
                   scheme=None, user=None, password=None, _restoring=False):
        # Enabling requires a valid address; disabling must never be blocked
        # by one (ps/wsl removal ignores the address, and git falls back to
        # the managed/detected URL), so only validate on the enable path.
        addr = self._endpoint_addr(host, port)
        if on:
            if not addr:
                return {"error": "代理地址或端口不合法，请先修正", "error_key": "errBadAddr"}
            h, p = addr
        else:
            h, p = addr if addr else (self.host, self.port)
        kw = self._proxy_kw(scheme, user, password)
        # Enabling applies the address: keep the cache in sync so later calls
        # without an explicit address (speed probe, restart_wsl) act on it.
        # Disabling must not touch the cache — its target address is whatever
        # is being removed, not the address the UI shows. Restoring a previous
        # proxy must not overwrite the UI's current fields either.
        if on and not _restoring:
            with self._speed_lock:
                self.host, self.port = h, p
            if scheme is not None:
                self.scheme = kw["scheme"]
            if user is not None:
                self.user = kw["user"]
            if password is not None:
                self.password = kw["password"]
        notes = []
        if on and not _restoring:
            self._stash_previous(target, self._managed_url(h, p, **kw))
        if (not on) and (not _restoring):
            restored = self._restore_previous(target)
            if restored is not None:
                return restored

        if target == "wsl":
            if on and h.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。")
            ret = pm.set_wsl_proxy(h, p, on, **kw)
            if ret is not True:
                return {"error": f"WSL: {ret}"}
            if on:
                notes.append("已写入 ~/.bashrc、~/.zshrc 与 fish conf.d，请新开终端后生效")
        elif target in ("gitwin", "gitwsl"):
            label = "Git(Windows)" if target == "gitwin" else "Git(WSL)"
            use_wsl = target == "gitwsl"
            if on:
                gh, gp = self._wsl_url_target(h, p) if use_wsl else (h, p)
                if use_wsl and (gh, gp) != (h, p):
                    notes.append("NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。")
                ret = pm.set_git_proxy(gh, gp, True, use_wsl=use_wsl, **kw)
                if ret is not True:
                    return {"error": f"{label}: {ret}"}
                self._git_managed[target] = self._managed_url(gh, gp, **kw)
            else:
                gh, gp, gkw = self._git_unset_target(target)
                ret = pm.set_git_proxy(gh, gp, False, use_wsl=use_wsl, **gkw)
                self._git_managed[target] = None
                if ret is not True:
                    return {"error": f"{label}: {ret}"}
        elif target in self._ENDPOINT_INFO:
            setter_name, label, detected_name, on_note = self._ENDPOINT_INFO[target]
            setter = getattr(pm, setter_name)
            if not on:
                h, p, kw = self._detected_hpk(getattr(pm, detected_name)(), h, p, kw)
            if target in ("npmwsl", "pipwsl") and on \
                    and h.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。")
            ret = setter(h, p, on, **kw)
            if target == "sys":
                ok, sys_note = self._sys_result(ret)
                if not ok:
                    return {"error": f"{label}: {ret}"}
                if on and sys_note:
                    notes.append(sys_note)
            elif ret is not True:
                return {"error": f"{label}: {ret}"}
            if on and on_note:
                notes.append(on_note)
        else:
            return {"error": f"未知端点 {target}"}
        if on and not _restoring:
            # Commit only after the endpoint write succeeded: a failed enable
            # must not make switch_port treat the still-old endpoints as ours.
            self.applied_host, self.applied_port = h, p
            self.applied_kw = dict(kw)
        return {"ok": True, "note": "；".join(notes) if notes else None}

    def _wsl_url_target(self, host, port):
        """Host/port that WSL-side endpoints should write: in NAT mode a
        loopback host is unreachable from the distro, so it becomes the
        Windows gateway IP (mirrored mode keeps the loopback address)."""
        port = str(port)
        if host.lower() in pm.LOOPBACK_HOSTS \
                and pm.get_wsl_network_mode() != "Mirrored":
            gw = pm.get_wsl_gateway_ip()
            if gw:
                return gw, port
        return host, port

    def _git_unset_target(self, target):
        """Address whose git proxy a disable should remove: this session's
        managed URL if known, else whatever git currently has configured
        (survives restarts, where _git_managed starts empty)."""
        stored = self._git_managed.get(target)
        parsed = self._adopt_parsed(stored)
        if parsed:
            return parsed["host"], parsed["port"], {
                "scheme": parsed["scheme"], "user": parsed["user"],
                "password": parsed["password"]}
        v = pm.get_git_proxy_win() if target == "gitwin" \
            else (self.refresh_item("wsl") or {}).get("git")
        parsed = self._adopt_parsed(v)
        if parsed:
            return parsed["host"], parsed["port"], {
                "scheme": parsed["scheme"], "user": parsed["user"],
                "password": parsed["password"]}
        return (self.host, self.port, self._proxy_kw())

    # ── quick port switch: re-apply enabled endpoints on the new port ──
    def switch_port(self, new_port, host=None, scheme=None, user=None, password=None):
        """Same mutation lock as do_toggle: a port switch must not interleave
        with a tray-quit clear or a plain toggle mid-migration."""
        with self._endpoint_mutation_lock:
            return self._switch_port(new_port, host, scheme, user, password)

    def _switch_port(self, new_port, host=None, scheme=None, user=None, password=None):
        new_port = str(new_port).strip()
        if not pm.is_valid_port(new_port):
            return {"error": "端口须为 1-65535 的整数"}
        new_port = str(int(new_port))
        new_host = str(host).strip() if host else self.host
        if not valid_host(new_host):
            return {"error": "代理地址不合法，请先修正"}
        new_kw = self._proxy_kw(scheme, user, password)
        if scheme is not None and not pm.is_valid_scheme(new_kw["scheme"]):
            return {"error": "代理协议不合法"}
        if (user is not None and not pm.is_valid_userinfo(new_kw["user"])) \
                or (password is not None and not pm.is_valid_userinfo(new_kw["password"])):
            return {"error": "用户名或密码含非法字符"}
        old_kw = dict(self.applied_kw)
        if new_port == self.applied_port and new_host == self.applied_host \
                and new_kw == old_kw:
            # Nothing to migrate, but re-sync the UI cache and the config
            # file: persistSoon may have persisted a field address that was
            # never applied to any endpoint.
            with self._speed_lock:
                self.host, self.port = new_host, new_port
            self.scheme, self.user, self.password = (
                new_kw["scheme"], new_kw["user"], new_kw["password"])
            with _cfg_lock:
                if not save_config(self._cfg_with_port(new_port)):
                    return {"ok": False, "error": "配置文件写入失败（磁盘已满或权限不足）"}
            return {"ok": True, "applied": []}

        # Match currently-applied endpoints against the LAST APPLIED address,
        # not the (possibly just-edited, possibly only saved) field host.
        # Re-enable uses new_host.
        old_h, old_p = self.applied_host, str(self.applied_port)
        if not valid_host(old_h) or not pm.is_valid_port(old_p):
            old_h, old_p = new_host, old_p if pm.is_valid_port(old_p) else new_port
        managed_url = pm.build_proxy_url(old_h, old_p, **old_kw)
        sys_url = pm.wininet_url(old_h, old_p, old_kw["scheme"])
        # The WSL npm/pip endpoints write the gateway URL in NAT mode, so the
        # "is this ours" comparison must use the same substituted URL.
        wsl_managed_url = managed_url
        if old_h.lower() in pm.LOOPBACK_HOSTS \
                and pm.get_wsl_network_mode() != "Mirrored":
            gw = pm.get_wsl_gateway_ip()
            if gw:
                wsl_managed_url = pm.build_proxy_url(gw, old_p, **old_kw)
        # Detect what is currently on. Only values equal to this tool's
        # managed URL count as "ours" — a proxy the user configured by hand
        # must survive the switch untouched (one wsl.exe call for the WSL side).
        wsl_state = self.refresh_item("wsl")
        enabled = {
            "ps": pm.get_ps_status() == managed_url,
            "sys": pm.get_sys_proxy() == sys_url,
            "wsl": bool((wsl_state or {}).get("on")),
            "gitwin": pm.get_git_proxy_win() == managed_url,
            "gitwsl": (wsl_state or {}).get("git") == wsl_managed_url,
            "npm": pm.get_npm_proxy() == managed_url,
            "pip": pm.get_pip_proxy() == managed_url,
            "npmwsl": pm.get_npm_proxy_wsl() == wsl_managed_url,
            "pipwsl": pm.get_pip_proxy_wsl() == wsl_managed_url,
        }
        if not any(enabled.values()):
            with self._speed_lock:
                self.host, self.port = new_host, new_port
            self.scheme, self.user, self.password = (
                new_kw["scheme"], new_kw["user"], new_kw["password"])
            self.applied_host, self.applied_port = new_host, new_port
            self.applied_kw = dict(new_kw)
            with _cfg_lock:
                if not save_config(self._cfg_with_port(new_port)):
                    return {"ok": False, "error": "配置文件写入失败（磁盘已满或权限不足）"}
            return {"ok": True, "applied": []}

        errs, notes = {}, []
        # Disable with the OLD port first (git's surgical unset needs the
        # exact managed URL), then re-enable each endpoint on the new one.
        gitwsl_unset = (old_h, old_p, old_kw)
        if enabled["gitwsl"]:
            gv = (wsl_state or {}).get("git")
            parsed = self._adopt_parsed(gv)
            if parsed:
                gitwsl_unset = (parsed["host"], parsed["port"], {
                    "scheme": parsed["scheme"], "user": parsed["user"],
                    "password": parsed["password"]})
        ctx = {"gitwsl_unset": gitwsl_unset}
        # Phase 1: disable all enabled endpoints with the old address.
        # Track successfully disabled targets so we can rollback on partial
        # failure (Issue 17).
        disabled_ok = []
        dis_errs = {}
        for tgt in self._SWITCH_TARGETS:
            if not enabled[tgt]:
                continue
            err = self._switch_disable_endpoint(
                tgt, old_h, old_p, old_kw, ctx)
            if err is None:
                disabled_ok.append(tgt)
            else:
                dis_errs[tgt] = err
        # Rollback: if any disable failed, re-enable the ones we already
        # disabled so the user's proxy state is not left half-broken.
        if dis_errs:
            for tgt in disabled_ok:
                self._switch_enable_endpoint(tgt, old_h, old_p, old_kw)
            return {"ok": False, "applied": [],
                    "error": "；".join(dis_errs.values()), "note": None}

        with self._speed_lock:
            self.host, self.port = new_host, new_port
        self.scheme, self.user, self.password = (
            new_kw["scheme"], new_kw["user"], new_kw["password"])
        # The switch committed to the new address once phase 1 succeeded;
        # a phase-2 endpoint failure leaves that endpoint OFF, not on the
        # old address, so applied_* still moves.
        self.applied_host, self.applied_port = new_host, new_port
        self.applied_kw = dict(new_kw)
        cfg_err = None
        with _cfg_lock:
            if not save_config(self._cfg_with_port(new_port)):
                cfg_err = "配置文件写入失败（磁盘已满或权限不足）"

        # Phase 2: re-enable each endpoint on the new address.
        applied = []
        for tgt in self._SWITCH_TARGETS:
            if not enabled[tgt]:
                continue
            err, git_url, extra_note = self._switch_enable_endpoint(
                tgt, new_host, new_port, new_kw)
            if err is None:
                applied.append(tgt)
                if git_url is not None:
                    self._git_managed[tgt] = git_url
                if extra_note:
                    notes.append(extra_note)
            else:
                errs[tgt] = err
        if "wsl" in applied or "npmwsl" in applied or "pipwsl" in applied \
                or "gitwsl" in applied:
            if new_host.lower() in pm.LOOPBACK_HOSTS \
                    and pm.get_wsl_network_mode() != "Mirrored":
                notes.append("NAT 模式已自动改用宿主网关 IP。")
        if cfg_err:
            errs["config"] = cfg_err
        ok = not errs
        return {"ok": ok, "applied": applied,
                "error": "；".join(errs.values()) if errs else None,
                "note": "；".join(notes) if notes else None}

    # ── switch_port helpers (Issues 8 & 17) ─────────────────────────────────
    _SWITCH_TARGETS = ("ps", "sys", "wsl", "gitwin", "gitwsl",
                       "npm", "pip", "npmwsl", "pipwsl")

    def _switch_disable_endpoint(self, target, old_h, old_p, old_kw, ctx):
        """Disable one endpoint during port switch.
        Returns None on success, error string on failure."""
        if target == "ps":
            ret = pm.set_ps_proxy(old_h, old_p, False, **old_kw)
            return None if ret is True else f"PowerShell: {ret}"
        if target == "sys":
            ret = pm.set_sys_proxy(old_h, old_p, False, **old_kw)
            ok, _note = self._sys_result(ret)
            return None if ok else f"系统代理: {ret}"
        if target == "wsl":
            ret = pm.set_wsl_proxy(old_h, old_p, False, **old_kw)
            return None if ret is True else f"WSL: {ret}"
        if target in ("gitwin", "gitwsl"):
            label = "Git(Windows)" if target == "gitwin" else "Git(WSL)"
            use_wsl = target == "gitwsl"
            uh, up, ukw = ctx["gitwsl_unset"] if target == "gitwsl" \
                else (old_h, old_p, old_kw)
            ret = pm.set_git_proxy(uh, up, False, use_wsl=use_wsl, **ukw)
            return None if ret is True else f"{label}: {ret}"
        if target == "npm":
            ret = pm.set_npm_proxy(old_h, old_p, False, **old_kw)
            return None if ret is True else f"npm: {ret}"
        if target == "pip":
            ret = pm.set_pip_proxy(old_h, old_p, False, **old_kw)
            return None if ret is True else f"pip: {ret}"
        if target in ("npmwsl", "pipwsl"):
            label = "npm(WSL)" if target == "npmwsl" else "pip(WSL)"
            wh, wp, wkw = old_h, old_p, old_kw
            getter = pm.get_npm_proxy_wsl if target == "npmwsl" \
                else pm.get_pip_proxy_wsl
            parsed = self._adopt_parsed(getter())
            if parsed:
                wh, wp, wkw = parsed["host"], parsed["port"], {
                    "scheme": parsed["scheme"], "user": parsed["user"],
                    "password": parsed["password"]}
            fn = pm.set_npm_proxy_wsl if target == "npmwsl" \
                else pm.set_pip_proxy_wsl
            ret = fn(wh, wp, False, **wkw)
            return None if ret is True else f"{label}: {ret}"
        return None

    def _switch_enable_endpoint(self, target, new_host, new_port, new_kw):
        """Enable one endpoint during port switch.
        Returns (error_or_None, git_managed_url_or_None, note_or_None)."""
        if target == "wsl":
            pm.invalidate_wsl_network_mode_cache()
            ret = pm.set_wsl_proxy(new_host, new_port, True, **new_kw)
            return (None if ret is True else f"WSL: {ret}", None, None)
        if target == "ps":
            ret = pm.set_ps_proxy(new_host, new_port, True, **new_kw)
            return (None if ret is True else f"PowerShell: {ret}", None, None)
        if target == "sys":
            ret = pm.set_sys_proxy(new_host, new_port, True, **new_kw)
            ok, pac_note = self._sys_result(ret)
            return (None if ok else f"系统代理: {ret}", None, pac_note)
        if target in ("gitwin", "gitwsl"):
            label = "Git(Windows)" if target == "gitwin" else "Git(WSL)"
            use_wsl = target == "gitwsl"
            gh, gp = self._wsl_url_target(new_host, new_port) \
                if use_wsl else (new_host, new_port)
            ret = pm.set_git_proxy(gh, gp, True, use_wsl=use_wsl, **new_kw)
            if ret is True:
                return (None, self._managed_url(gh, gp, **new_kw), None)
            return (f"{label}: {ret}", None, None)
        if target == "npm":
            ret = pm.set_npm_proxy(new_host, new_port, True, **new_kw)
            return (None if ret is True else f"npm: {ret}", None, None)
        if target == "pip":
            ret = pm.set_pip_proxy(new_host, new_port, True, **new_kw)
            return (None if ret is True else f"pip: {ret}", None, None)
        if target in ("npmwsl", "pipwsl"):
            label = "npm(WSL)" if target == "npmwsl" else "pip(WSL)"
            fn = pm.set_npm_proxy_wsl if target == "npmwsl" \
                else pm.set_pip_proxy_wsl
            ret = fn(new_host, new_port, True, **new_kw)
            return (None if ret is True else f"{label}: {ret}", None, None)
        return (None, None, None)

    def _cfg_with_port(self, new_port):
        cfg = load_config()
        cfg["host"], cfg["port"] = self.host, new_port
        cfg["scheme"], cfg["user"], cfg["password"] = (
            self.scheme, self.user, self.password)
        return cfg

    # ── live speed + port probe (sampled on a background thread) ──
    def _speed_loop(self):
        last_t = time.monotonic()
        while True:
            time.sleep(1.0)
            try:
                now = time.monotonic()
                dt = max(now - last_t, 1e-3)
                last_t = now
                cur = pm.get_net_counters()
                up_str = dn_str = None
                dn_num = 0.0
                with self._speed_lock:
                    # Snapshot host/port atomically under the same lock that
                    # do_toggle / switch_port acquire when writing the pair.
                    host, port = self.host, self.port
                    if cur is not None and self._speed_prev is not None:
                        up = (cur[0] - self._speed_prev[0]) / dt
                        dn = (cur[1] - self._speed_prev[1]) / dt
                        if 0 <= up <= 100 * 1024 * 1024:
                            up_str = pm.format_speed(up)
                        if 0 <= dn <= 100 * 1024 * 1024:
                            dn_str = pm.format_speed(dn)
                            dn_num = float(dn)
                    self._speed_prev = cur if cur is not None else self._speed_prev
                # TCP probe can block ~1.5s when the proxy is down — keep it
                # off the speed lock so get_speed() stays responsive.
                alive = None
                probed_port = ""
                if host and pm.is_valid_port(port):
                    alive = pm.proxy_port_alive(host, port)
                    probed_port = str(int(port))
                with self._speed_lock:
                    self._speed = {
                        "up": up_str or "—", "dn": dn_str or "—",
                        "dn_num": dn_num,
                        "iface": pm._primary_iface or "",
                        "alive": alive,
                        "port": probed_port,
                    }
            except Exception:
                pm.log_exception_once("speed_loop", "speed_loop")

    def get_speed(self):
        with self._speed_lock:
            return dict(self._speed)

    # ── misc actions ──
    def restart_wsl(self):
        # 两段式加锁：pm.restart_wsl（shutdown + 最多 3 次探活）可阻塞数十
        # 秒，绝不持 _endpoint_mutation_lock；重启用完后的"读取共享状态 →
        # 重写 WSL 侧端点"整体在锁内 —— 与 do_toggle 持锁执行慢速端点写入
        # 的既有设计一致，"锁外"原则只针对 pm.restart_wsl 子进程本身。
        # host/port 按锁序（mutation → speed）读取。
        pm.invalidate_wsl_network_mode_cache()
        ret = pm.restart_wsl()
        if ret is not True:
            return {"ok": False, "error": str(ret)}
        # A WSL restart can move the NAT subnet; refresh the gateway IP baked
        # into every WSL-side endpoint (bashrc, git, npm, pip) when the proxy
        # is on with a loopback host.
        with self._endpoint_mutation_lock:
            with self._speed_lock:
                host, port = self.host, self.port
            if host.lower() not in pm.LOOPBACK_HOSTS \
                    or not pm.is_valid_port(port) \
                    or pm.get_wsl_network_mode() == "Mirrored":
                return {"ok": True, "error": None}
            # 端点开关状态必须在写回段的 mutation 锁内（重）读：读取与写回
            # 之间若有 clear_all_endpoints/do_toggle 把端点关闭，用锁外读到
            # 的陈旧 "on" 状态会把刚关掉的端点重新启用。锁内执行 wsl.exe
            # 慢速读与 do_toggle 持锁写端点同模式。
            wsl_state = self.refresh_item("wsl")
            npm = pm.get_npm_proxy_wsl()
            pipv = pm.get_pip_proxy_wsl()
            with self._speed_lock:
                host, port = self.host, self.port
            h, p = host, str(int(port))
            kw = self._proxy_kw()
            if wsl_state and wsl_state.get("on"):
                pm.set_wsl_proxy(h, p, True, **kw)
            git = (wsl_state or {}).get("git")
            if self._adopt_parsed(git):
                gh, gp = self._wsl_url_target(h, p)
                pm.set_git_proxy(gh, gp, True, use_wsl=True, **kw)
                self._git_managed["gitwsl"] = self._managed_url(gh, gp, **kw)
            if self._adopt_parsed(npm):
                pm.set_npm_proxy_wsl(h, p, True, **kw)
            if self._adopt_parsed(pipv):
                pm.set_pip_proxy_wsl(h, p, True, **kw)
        return {"ok": True, "error": None}

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
            pm.log_exception("copy")
            return False

    def test_channel(self, which):
        """Connectivity probe for one proxy variable chip. which selects the
        proxy source:
          ps / ps_all / gitwin / npm / pip  -> Windows user proxy sources
          wsl / wsl_all / gitwsl / npmwsl / pipwsl -> WSL-side sources
        The probe fetches a lightweight URL THROUGH the corresponding proxy
        (source read from the same config files/env the tool manages) and
        reports success + latency. Direct (no-proxy) probes are NOT done.
        """
        import time as _t
        import urllib.request as _urlreq

        sources = {
            # Windows side. The _https variants probe the HTTPS_PROXY variable
            # itself, so a hand-configured https:// proxy is tested as such
            # instead of silently reporting the HTTP row's result. The _all
            # variants do the same for ALL_PROXY.
            "ps":     pm.get_ps_proxy_vars,      # env vars (HTTP_PROXY)
            "ps_https": lambda: {"HTTPS_PROXY":
                                 pm.get_ps_proxy_vars()["HTTPS_PROXY"]},
            "ps_all": lambda: {"ALL_PROXY":
                               pm.get_ps_proxy_vars()["ALL_PROXY"]},
            "sys":    lambda: {"HTTP_PROXY": pm.get_sys_proxy()},
            "gitwin": lambda: {"HTTP_PROXY": pm.get_git_proxy_win()},
            "npm":    lambda: {"HTTP_PROXY": pm.get_npm_proxy()},
            "pip":    lambda: {"HTTP_PROXY": pm.get_pip_proxy()},
        }
        fn = sources.get(which)
        if fn:
            vars_ = fn()
            url = vars_.get("HTTP_PROXY") or vars_.get("HTTPS_PROXY") or vars_.get("ALL_PROXY")
            parsed = self._adopt_parsed(url)
            if not parsed:
                return {"ok": False, "error": "未设置代理，无法测试"}
            if not valid_host(parsed["host"]):
                return {"ok": False, "error": f"代理地址无效：{url}"}
            return self._proxy_probe(str(url).strip(), parsed, _t, _urlreq)
        # WSL side: the proxy URL lives inside the distro; probe THROUGH it
        # from within WSL so the path matches what npm/pip/git will use.
        wsl_state = self.refresh_item("wsl") if which == "gitwsl" else None
        wsl_sources = {
            "wsl":    lambda: pm.get_wsl_proxy_vars(),
            "wsl_https": lambda: {"HTTPS_PROXY":
                                  pm.get_wsl_proxy_vars()["HTTPS_PROXY"]},
            "wsl_all": lambda: {"ALL_PROXY":
                                pm.get_wsl_proxy_vars()["ALL_PROXY"]},
            "gitwsl": lambda: {"HTTP_PROXY": (wsl_state or {}).get("git", pm.UNSET)},
            "npmwsl": lambda: {"HTTP_PROXY": pm.get_npm_proxy_wsl()},
            "pipwsl": lambda: {"HTTP_PROXY": pm.get_pip_proxy_wsl()},
        }
        wfn = wsl_sources.get(which)
        if not wfn:
            return {"ok": False, "error": f"未知渠道 {which}"}
        result = wfn() or {}
        url = result.get("HTTP_PROXY") or result.get("HTTPS_PROXY") or result.get("ALL_PROXY")
        parsed = self._adopt_parsed(url)
        if not parsed:
            return {"ok": False, "error": "未设置代理，无法测试"}
        if not valid_host(parsed["host"]):
            return {"ok": False, "error": f"代理地址无效：{url}"}
        return self._wsl_proxy_probe(str(url).strip(), parsed)

    def _proxy_probe(self, proxy_url, parsed, _t, urlreq):
        """Fetch a lightweight URL through proxy_url. Prefers curl (HTTP and
        SOCKS); falls back to urllib for http:// only."""
        import shutil
        curl = shutil.which("curl") or shutil.which("curl.exe")
        if curl:
            return self._curl_probe(curl, proxy_url)
        if parsed["scheme"].startswith("socks"):
            return {"ok": False, "error": "SOCKS 测试需要系统 curl"}
        proxy = urlreq.ProxyHandler({
            "http": proxy_url, "https": proxy_url})
        opener = urlreq.build_opener(proxy)
        last_err = "连接失败"
        t0 = _t.perf_counter()
        for dest in PROBE_URLS:
            try:
                req = urlreq.Request(
                    dest, headers={"User-Agent": "ProxyManager/2"})
                with opener.open(req, timeout=5) as resp:
                    ok = resp.status in (200, 204)
                ms = int((_t.perf_counter() - t0) * 1000)
                if ok:
                    return {"ok": True, "ms": ms}
                last_err = f"HTTP {resp.status}"
            except Exception as e:
                last_err = str(e)[:160] or "连接失败"
        return {"ok": False, "error": last_err}

    def _curl_probe(self, curl, proxy_url):
        last_err = "连接失败"
        for dest in PROBE_URLS:
            try:
                r = subprocess.run(
                    [curl, "-x", proxy_url, "-s", "-o", "NUL" if os.name == "nt" else "/dev/null",
                     "-w", "%{http_code} %{time_total}", "--max-time", "5", dest],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                parts = (r.stdout or "").strip().split()
                if len(parts) >= 2 and parts[0] in ("200", "204"):
                    return {"ok": True, "ms": int(float(parts[1]) * 1000)}
                last_err = f"HTTP {parts[0]}" if parts else "连接失败"
            except Exception as e:
                last_err = str(e)[:160] or "连接失败"
        return {"ok": False, "error": last_err}

    def _wsl_proxy_probe(self, proxy_url, parsed):
        """Same probe executed inside WSL via curl (through the proxy)."""
        # Escape single quotes for bash safety even though build_proxy_url
        # percent-encodes userinfo (defense-in-depth).
        sq_url = proxy_url.replace("'", "'\\''")
        urls = " ".join(PROBE_URLS)
        script = (
            f"for u in {urls}; do "
            f"out=$(curl -x '{sq_url}' -s -o /dev/null "
            f"-w '%{{http_code}} %{{time_total}}' --max-time 5 \"$u\" 2>/dev/null); "
            "code=${out%% *}; "
            "if [ \"$code\" = 204 ] || [ \"$code\" = 200 ]; then echo \"$out\"; exit 0; fi; "
            "done; echo FAIL; echo"
        )
        try:
            r = subprocess.run(
                ["wsl", "-e", "bash", "-c", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
                creationflags=subprocess.CREATE_NO_WINDOW)
            out = (r.stdout or "").strip().split()
            if len(out) >= 2 and out[0] in ("204", "200"):
                return {"ok": True, "ms": int(float(out[1]) * 1000)}
            if not pm.wsl_available():
                return {"ok": False, "error": "WSL 不可用"}
            return {"ok": False, "error": f"HTTP {out[0]}" if out else "连接失败"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:120] or "连接失败"}

    def apply_form_back(self, mode):
        """Re-paint the WinForms Form background to match the theme. The form
        shows through the 9px padded ring used for edge resizing; its color is
        only chosen at window creation, so switching to dark at runtime left
        a bright ring around the page. Same marshaling as
        _apply_window_padding (Invoke to the UI thread)."""
        try:
            form = getattr(self._window, "native", None) if self._window else None
            if form is None:
                return False
            from System import Type, Func  # noqa: E402
            from System.Drawing import Color  # noqa: E402

            def _set():
                form.BackColor = Color.FromArgb(0x1B, 0x1B, 0x21) if mode == "dark" \
                    else Color.FromArgb(0xF7, 0xF6, 0xFB)

            if form.InvokeRequired:
                form.Invoke(Func[Type](_set))
            else:
                _set()
            return True
        except Exception:
            pm.log_exception("apply_form_back")
            if os.environ.get("PM_DEBUG"):
                print("[pm] form back recolor failed", flush=True)
            return False

    # ── custom background ──
    def pick_background(self):
        """Native file picker via PowerShell (process-isolated, so no COM
        apartment headaches inside the pywebview process). stdout is forced
        to UTF-8 so Chinese paths survive the console round-trip."""
        # A topmost window would cover the dialog (it is another process).
        was_pin = self._pinned
        if was_pin:
            self._apply_topmost(False)
        ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
              "$f = New-Object System.Windows.Forms.OpenFileDialog; "
              "$f.Filter = '图片|" + _BG_FILTER + "'; "
              "$f.Title = '选择背景图片'; "
              "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.FileName }")
        import tempfile
        ps_path = None
        try:
            # Write script to a temp file and invoke with -File instead of
            # -Command so PowerShell does not interpret arbitrary strings.
            fd, ps_path = tempfile.mkstemp(suffix=".ps1")
            with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
                f.write(ps)
            r = __import__("subprocess").run(
                ["powershell", "-NoProfile", "-STA", "-File", ps_path],
                capture_output=True, timeout=120, encoding="utf-8",
                errors="replace", creationflags=0x08000000)
            path = (r.stdout or "").strip().splitlines()
            path = path[-1].strip() if path else ""
            if not path or not os.path.isfile(path):
                return {"ok": False}  # user cancelled
            try:
                if os.path.getsize(path) > MAX_BG_BYTES:
                    return {"ok": False, "error": "图片超过 8MB，请选择更小的文件"}
            except OSError as e:
                return {"ok": False, "error": str(e)}
            if not self.save_config({"bg_path": path}):
                return {"ok": False, "error": "配置文件写入失败（磁盘已满或权限不足）"}
            return {"ok": True, "path": path, "name": os.path.basename(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            if ps_path:
                try:
                    os.unlink(ps_path)
                except OSError:
                    pass
            if was_pin:
                self._apply_topmost(True)

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
        if stat.st_size > MAX_BG_BYTES:
            return {"error": "图片超过 8MB，请选择更小的文件"}
        if self._bg_cache and self._bg_cache[0] == key:
            return {"data": self._bg_cache[1]}
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                "webp": "webp", "bmp": "bmp", "gif": "gif"}.get(ext)
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
        elif cmd == "drag_move":
            # JS starts the NATIVE move loop on mousedown (like a real
            # caption): drag works with zero delay, and a fast second click
            # becomes WM_NCLBUTTONDBLCLK (handled in _resize_hook_proc) —
            # both behaviors come from the system, not from JS timers.
            # lParam MUST carry the real cursor position (0,0 would make the
            # move loop compute offsets from the top-left corner and the
            # window would jump). Must run on the UI thread.
            try:
                form = getattr(w, "native", None)
                if form is None:
                    return {"ok": False}
                from System import Type, Func  # noqa: E402

                def _move():
                    if _is_maximized():
                        return  # dragging a maximized window is a no-op
                    hwnd = self._hwnd()
                    if not hwnd:
                        return
                    pt = _POINT()
                    if not user32.GetCursorPos(ctypes.byref(pt)):
                        return
                    # ReleaseCapture so the move loop owns the mouse.
                    user32.ReleaseCapture()
                    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
                    if lp & 0x80000000:
                        lp -= 0x100000000  # sign-extend (negative coords)
                    # WM_NCLBUTTONDOWN with HTCAPTION enters the system move
                    # loop; it returns when the button is released.
                    user32.SendMessageW(hwnd, 0x00A1, 2, lp)

                if form.InvokeRequired:
                    form.Invoke(Func[Type](_move))
                else:
                    _move()
            except Exception:
                pass
            return {"ok": True}
        elif cmd == "close":
            if self._tray_ok:
                self._save_geometry()
                w.hide()  # hide to tray, mirroring the tkinter app
            else:
                # No tray: hiding would orphan the process. Honor 退出时关闭
                # 代理 here too — the tray 退出 path must not be the only one.
                try:
                    self._save_geometry()
                    if load_config().get("quit_clear"):
                        self.clear_all_endpoints()
                except Exception:
                    pass
                w.destroy()
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

    def _toggle_maximize(self, keep_prev=False):
        """Frameless forms cover the taskbar when natively maximized, so
        maximize = resize to the monitor work area, restore = prev bounds.

        keep_prev: the tray geometry restore seeds _prev_bounds with the
        SAVED bounds first (a fresh window still sits at its default size)
        and passes keep_prev=True so this capture cannot clobber them.

        NEVER call evaluate_js from here: window_control reaches this on a
        js_api worker thread, and evaluate_js from a worker deadlocks the UI
        thread (same class of hang as set_on_top — see module docstring).
        The taskbar path (WndProc, UI thread) pushes the icon sync itself."""
        hwnd = self._hwnd()
        if not hwnd:
            return
        if not self._maximized:
            if not keep_prev:
                rect = _RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                self._prev_bounds = (rect.left, rect.top, rect.right,
                                     rect.bottom)
            wa = _RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
            user32.SetWindowPos(hwnd, 0, wa.left, wa.top,
                                wa.right - wa.left, wa.bottom - wa.top, 0x0004)  # NOZORDER
            self._maximized = True
        else:
            if self._prev_bounds:
                left, top, right, bottom = self._prev_bounds
                user32.SetWindowPos(hwnd, 0, left, top,
                                    right - left, bottom - top, 0x0004)
            self._maximized = False
        set_resize_maximized(self._maximized)
        self._save_geometry()

    def _save_geometry(self):
        """Persist window bounds so the next launch reopens in place. While
        fake-maximized, the PRE-maximize bounds are saved alongside win_max
        so a fresh start maximizes directly and a restore lands correctly."""
        try:
            hwnd = self._hwnd()
            if not hwnd:
                return
            # Minimized windows report iconized coordinates (≈ -32000) from
            # GetWindowRect — skip the save so we don't persist garbage.
            if user32.IsIconic(hwnd):
                return
            r = _RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return
            self._win_max = bool(self._maximized)
            if self._win_max and self._prev_bounds:
                rect = [int(v) for v in self._prev_bounds]
            else:
                rect = [r.left, r.top, r.right, r.bottom]
            self._win_rect = rect
            save_config({"win_rect": rect, "win_max": self._win_max})
        except Exception:
            pm.log_exception("save_geometry")

    def _clear_maximized_state(self):
        """Called from the WndProc hook (via _clear_fake_maximized) when a
        native caption drag restored the window behind our back: the bounds
        are already correct, only the instance flag must follow."""
        self._maximized = False

