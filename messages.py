"""Bilingual message catalog shared by the backend modules and the tests.

Backend code builds user-facing strings as ``LStr``/``LJoin``: the ``str``
value is the zh rendering (backward-compatible for logs and existing tests),
while ``.key``/``.params`` ride along so the web UI can render the message in
the UI language via the same key in ``ui/i18n.js``. pywebview serializes an
``LStr`` as a plain string, so every bridge payload that carries one must
also attach the explicit ``error_parts`` / ``note_parts`` fields (see
js_api._endpoint_err and friends).

Every key here MUST exist in BOTH tables of ui/i18n.js — enforced by
tests/test_messages.py so the two catalogs cannot drift apart.
"""

TEMPLATES = {
    # ── notes (success paths) ──────────────────────────────────────────────
    "psNewTerm": {
        "zh": "请新开一个终端后生效",
        "en": "Open a new terminal for it to take effect",
    },
    "wslWrittenNote": {
        "zh": "已写入 ~/.bashrc、~/.zshrc 与 fish conf.d，请新开终端后生效",
        "en": "Written to ~/.bashrc, ~/.zshrc, and fish conf.d — open a new terminal to apply",
    },
    "natRewritten": {
        "zh": "NAT 模式下 WSL 无法访问 127.0.0.1，已自动改用宿主网关 IP。",
        "en": "NAT mode: WSL cannot reach 127.0.0.1 — the host gateway IP was used instead.",
    },
    "natNoGateway": {
        "zh": "NAT 模式下无法获取宿主网关 IP，仍写入 127.0.0.1，WSL 内代理可能不可达",
        "en": "NAT mode: could not get the host gateway IP; wrote 127.0.0.1 — the proxy may be unreachable inside WSL",
    },
    "sysPacNote": {
        "zh": "检测到 PAC 自动配置脚本（{url}），浏览器可能仍按 PAC 规则分流，静态系统代理未必生效",
        "en": "PAC auto-config script detected ({url}); browsers may keep following PAC rules, so the static system proxy may not apply",
    },
    "sysPerMachineNote": {
        "zh": "检测到 ProxySettingsPerUser=0（按机器策略管理代理）：本工具写入的用户级代理可能不生效",
        "en": "ProxySettingsPerUser=0 detected (machine-wide proxy policy): the user-level proxy written by this tool may not take effect",
    },

    # ── errors: WSL writes ────────────────────────────────────────────────
    "errNoWslWhere": {
        "zh": "{where}未检测到可用的 WSL 发行版",
        "en": "{where}No usable WSL distro detected",
    },
    "errWslWriteDetail": {
        "zh": "{where}写入 WSL 代理配置失败：{detail}",
        "en": "{where}Failed to write the WSL proxy config: {detail}",
    },
    "errWslWriteHint": {
        "zh": "{where}写入 WSL 代理配置失败（权限不足或磁盘已满？）",
        "en": "{where}Failed to write the WSL proxy config (permission denied, or disk full?)",
    },
    "errNoGitWslWhere": {
        "zh": "{where}未找到 git",
        "en": "{where}git not found",
    },
    "errGitFailWslWhere": {
        "zh": "{where}git 配置失败（exit {code}）",
        "en": "{where}git config failed (exit {code})",
    },
    "errWslReadFail": {
        "zh": "{where}读取 {path} 失败（exit {code}）",
        "en": "{where}Failed to read {path} (exit {code})",
    },
    "errWslWriteFileFail": {
        "zh": "{where}写入 {path} 失败（exit {code}）",
        "en": "{where}Failed to write {path} (exit {code})",
    },

    # ── errors: git ───────────────────────────────────────────────────────
    # errNoGit already exists in ui/i18n.js and is reused here.
    "errNoGitInWsl": {
        "zh": "WSL 内未找到 git",
        "en": "git not found inside WSL",
    },

    # ── errors: WSL restart ───────────────────────────────────────────────
    "errRestartNoWsl": {
        "zh": "未检测到可用的 WSL 发行版（{err}）",
        "en": "No usable WSL distro detected ({err})",
    },

    # ── errors: restore stash ─────────────────────────────────────────────
    "errStashIncomplete": {
        "zh": "暂存数据不完整",
        "en": "Stash data is incomplete",
    },
    "errStashDroppedCorrupt": {
        "zh": "暂存数据不完整且无法恢复，已从恢复列表移除",
        "en": "Stash data was incomplete and unrecoverable; removed from the restore list",
    },
    "errStashDroppedDpapi": {
        "zh": "暂存数据无法解密（DPAPI），已移除暂存；端点现在可以直接关闭",
        "en": "Stash could not be decrypted (DPAPI); stash removed — the endpoint can now be disabled",
    },
    "errRestoreFail": {
        "zh": "恢复覆盖前的代理失败",
        "en": "Failed to restore the previous proxy",
    },

    # ── errors: validation / misc ─────────────────────────────────────────
    # badPort / badUser / errBadAddr / saveFail / errDpapi already exist in
    # ui/i18n.js and are reused by the backend.
    "errBadScheme": {
        "zh": "代理协议不合法",
        "en": "Invalid proxy scheme",
    },
    "errUnknownEndpoint": {
        "zh": "未知端点 {target}",
        "en": "Unknown endpoint {target}",
    },

    # ── errors: connectivity test ─────────────────────────────────────────
    "errNoProxyToTest": {
        "zh": "未设置代理，无法测试",
        "en": "No proxy configured; nothing to test",
    },
    "errBadProxyUrl": {
        "zh": "代理地址无效：{url}",
        "en": "Invalid proxy URL: {url}",
    },
    "errSocksNeedsCurl": {
        "zh": "SOCKS 测试需要系统 curl",
        "en": "SOCKS testing requires curl on PATH",
    },
    "errConnFail": {
        "zh": "连接失败",
        "en": "Connection failed",
    },
    "errWslUnavailable": {
        "zh": "WSL 不可用",
        "en": "WSL is unavailable",
    },
    "errUnknownChannel": {
        "zh": "未知渠道 {which}",
        "en": "Unknown channel {which}",
    },

    # ── errors: background image ──────────────────────────────────────────
    "errBgTooBig": {
        "zh": "图片超过 8MB，请选择更小的文件",
        "en": "Image exceeds 8 MB — choose a smaller file",
    },
    "bgPickDialogTitle": {
        "zh": "选择背景图片",
        "en": "Choose a background image",
    },
    # The extension list itself is appended by the caller (js_api).
    "bgPickDialogFilter": {
        "zh": "图片",
        "en": "Images",
    },
    "errBadUserinfo": {
        "zh": "用户名或密码含非法字符",
        "en": "Username or password contains invalid characters",
    },

    # ── endpoint labels used to prefix plain (OS/git) error strings ───────
    # The other labels ("PowerShell", "Git(Windows)", "WSL(Ubuntu)", …) are
    # already ASCII in both languages; only the system-proxy label is zh.
    "labelSys": {
        "zh": "系统代理",
        "en": "System proxy",
    },
}


class LStr(str):
    """A user-facing message: the str value is the zh rendering, while
    .key/.params let the web UI render it in the UI language."""

    def __new__(cls, key, params=None):
        tpl = TEMPLATES[key]["zh"]
        # Always .format(): templates with literal braces use the {{...}}
        # escape, which only collapses when format() actually runs. Empty
        # params still triggers it (no placeholders → no-op otherwise).
        value = tpl.format(**(params or {}))
        s = str.__new__(cls, value)
        s.key = key
        s.params = dict(params or {})
        return s

    def __repr__(self):
        return f"LStr({self.key!r}, {self.params!r})"


class LJoin(str):
    """Join several LStr parts with `sep`; .parts keeps them addressable so
    the web UI can render each part in the UI language."""

    def __new__(cls, parts, sep="; "):
        s = str.__new__(cls, sep.join(str(p) for p in parts))
        s.parts = list(parts)
        return s

    def __repr__(self):
        return f"LJoin({self.parts!r})"


def part_fields(x):
    """{key, params} for an LStr, else None (plain strings carry no key)."""
    if isinstance(x, LStr):
        return {"key": x.key, "params": dict(x.params)}
    return None


def parts_of(x):
    """[{key, params}] for an LStr/LJoin, else None."""
    if isinstance(x, LJoin):
        fields = [part_fields(p) for p in x.parts]
        return [f for f in fields if f] or None
    f = part_fields(x)
    return [f] if f else None
