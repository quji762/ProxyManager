"""NO_PROXY merge/strip must preserve user entries and only manage ours."""

from unittest.mock import patch

from proxy_manager import NO_PROXY_VALUE, _merged_no_proxy, _stripped_no_proxy


def test_merge_adds_loopback_without_clobber():
    merged = _merged_no_proxy("example.com,.corp")
    assert merged.startswith("example.com,.corp")
    for e in NO_PROXY_VALUE.split(","):
        assert e in merged.split(",")


def test_merge_does_not_duplicate():
    merged = _merged_no_proxy("localhost,127.0.0.1")
    assert merged.split(",").count("localhost") == 1


def test_strip_keeps_user_entries():
    existing = "example.com,localhost,127.0.0.1,::1,.corp"
    kept = _stripped_no_proxy(existing)
    assert "example.com" in kept
    assert ".corp" in kept
    assert "localhost" not in kept.split(",")
    assert "127.0.0.1" not in kept.split(",")


def test_merge_dedup_is_case_insensitive():
    """Hosts are case-insensitive: LOCALHOST must not duplicate localhost."""
    merged = _merged_no_proxy("LOCALHOST,127.0.0.1")
    lowered = [e.lower() for e in merged.split(",")]
    assert lowered.count("localhost") == 1
    # 用户原有大小写保持不变（不回写为我们的小写形式）
    assert "LOCALHOST" in merged.split(",")


def test_strip_matches_case_insensitively():
    """The user wrote LocalHost; a disable must still remove our entry."""
    kept = _stripped_no_proxy("LocalHost,127.0.0.1,EXAMPLE.com")
    assert kept == "EXAMPLE.com"


def test_sys_proxy_server_to_url_http_and_socks():
    from proxy_manager import _sys_proxy_server_to_url
    assert _sys_proxy_server_to_url("127.0.0.1:7897") == "http://127.0.0.1:7897"
    assert _sys_proxy_server_to_url("socks=127.0.0.1:7891") == "socks5://127.0.0.1:7891"
    assert _sys_proxy_server_to_url("http=127.0.0.1:7890;https=127.0.0.1:7890") == \
        "http://127.0.0.1:7890"
    assert _sys_proxy_server_to_url("") is None


# ── PAC (AutoConfigURL) awareness ─────────────────────────────────────────────
# Fake registry: get_sys_proxy / set_sys_proxy must never touch the real
# WinINET values in tests.
import proxy_manager as pm  # noqa: E402
import js_api  # noqa: E402

_INET = "Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings"


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patched_inet(state):
    """Patch pm.winreg so Internet Settings reads/writes hit `state`."""

    def fake_open(root, path, *a, **k):
        assert path == _INET
        return _FakeKey()

    def fake_query(key, name):
        if name not in state:
            raise FileNotFoundError(name)
        return state[name], None

    def fake_set(key, name, _reserved, _type, value):
        state[name] = value

    def fake_delete(key, name):
        state.pop(name, None)

    return patch.object(pm.winreg, "OpenKey", fake_open), \
        patch.object(pm.winreg, "QueryValueEx", fake_query), \
        patch.object(pm.winreg, "SetValueEx", fake_set), \
        patch.object(pm.winreg, "DeleteValue", fake_delete), \
        patch.object(pm.winreg, "CloseKey", lambda k: None), \
        patch.object(pm, "_wininet_notify", lambda: None)


def _fake_inet_state(**extra):
    state = {"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""}
    state.update(extra)
    return state


def test_set_sys_proxy_with_pac_returns_warning_note():
    """Enabling while a PAC (AutoConfigURL) is registered still writes the
    static proxy but returns (True, note) instead of a bare True."""
    state = _fake_inet_state(AutoConfigURL="http://pac.local/wpad.dat")
    patches = _patched_inet(state)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = pm.set_sys_proxy("127.0.0.1", "7897", True)
    assert isinstance(r, tuple) and r[0] is True and "PAC" in r[1]
    assert state["ProxyEnable"] == 1            # static proxy still written


def test_set_sys_proxy_without_pac_returns_true():
    state = _fake_inet_state()
    patches = _patched_inet(state)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert pm.set_sys_proxy("127.0.0.1", "7897", True) is True
        assert pm.set_sys_proxy("127.0.0.1", "7897", False) is True


def test_get_sys_proxy_ex_reports_pac_state():
    state = _fake_inet_state(AutoConfigURL="http://pac.local/wpad.dat")
    patches = _patched_inet(state)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        ex = pm.get_sys_proxy_ex()
        assert ex["pac"] is True
        assert ex["autoconfig_url"] == "http://pac.local/wpad.dat"
        assert ex["url"] == pm.UNSET
        pm.set_sys_proxy("127.0.0.1", "7897", True)
        ex = pm.get_sys_proxy_ex()
        assert ex["url"] == "http://127.0.0.1:7897"


def test_get_sys_proxy_stays_backward_compatible():
    """Plain get_sys_proxy must keep returning the URL string (many callers
    compare it against a managed URL)."""
    state = _fake_inet_state(AutoConfigURL="http://pac.local/wpad.dat")
    patches = _patched_inet(state)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        pm.set_sys_proxy("127.0.0.1", "7897", True)
        assert pm.get_sys_proxy() == "http://127.0.0.1:7897"


def _api_for_inet():
    """An Api whose only real dependency is the patched winreg layer."""
    with patch("proxy_manager.get_net_counters", return_value=None):
        return js_api.Api()


def test_get_sys_proxy_server_raw_ignores_enable_flag():
    """ProxyEnable=0 with a ProxyServer left behind is exactly what Windows
    leaves after the user turns the proxy off in the UI — get_sys_proxy()
    says UNSET there, the raw reader must still surface the address."""
    state = {"ProxyEnable": 0, "ProxyServer": "corp:8080", "ProxyOverride": ""}
    patches = _patched_inet(state)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert pm.get_sys_proxy() == pm.UNSET
        assert pm.get_sys_proxy_server_raw() == "corp:8080"


def test_set_sys_proxy_server_leaves_enable_flag_alone():
    """Restoring a remembered address must not re-arm the proxy: the server
    is written verbatim ('socks=…' included) and ProxyEnable is untouched."""
    state = _fake_inet_state(ProxyEnable=1, ProxyServer="corp:8080")
    patches = _patched_inet(state)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert pm.set_sys_proxy_server("socks=corp:1080") is True
        assert state["ProxyServer"] == "socks=corp:1080"
        assert state["ProxyEnable"] == 1
        assert pm.set_sys_proxy_server("") is True
        assert "ProxyServer" not in state       # '' deletes the value
        assert state["ProxyEnable"] == 1


def test_sys_residual_server_is_restored_after_enable_disable():
    """End-to-end for the residual case: the user's proxy was OFF but the
    server address was still in the registry. The enable stashes that
    residual (it is not ours), the disable's DeleteValue('ProxyServer')
    destroys it, and the restore puts the exact string back — while
    ProxyEnable stays 0, so the proxy is NOT re-armed.

    Api level on purpose: the disable branch of set_sys_proxy still deletes
    ProxyServer (by design), only the restore path re-writes it."""
    state = {"ProxyEnable": 0, "ProxyServer": "corp:8080", "ProxyOverride": ""}
    patches = _patched_inet(state)
    api = _api_for_inet()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert js_api.pm.get_sys_proxy() == pm.UNSET
        assert js_api.pm.get_sys_proxy_server_raw() == "corp:8080"
        assert api.do_toggle("sys", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore["sys"] == {"url": None, "override": None,
                                       "server": "corp:8080"}
        assert api.do_toggle("sys", False)["ok"] is True

    assert state["ProxyServer"] == "corp:8080"   # user's address back verbatim
    assert state["ProxyEnable"] == 0             # …and the proxy stays off


def test_sys_stash_skips_our_own_residual():
    """Our own leftover — we enabled 127.0.0.1:7897 and the user then turned
    the proxy off in the Windows UI — must never be stashed: the disable
    would write it back and re-arm a proxy the user just switched off."""
    api = _api_for_inet()
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    state = {"ProxyEnable": 0, "ProxyServer": "127.0.0.1:7897",
             "ProxyOverride": ""}
    patches = _patched_inet(state)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert api.do_toggle("sys", True, "127.0.0.1", "7897")["ok"] is True

    stashed = api._restore.get("sys")
    assert stashed is None or "server" not in stashed
