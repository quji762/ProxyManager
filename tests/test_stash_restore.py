"""Stash/restore regression tests: this tool must never stash (and later
"restore") proxy values it wrote itself, and a corrupted stash must not be
silently "restored" as an empty state."""

from unittest.mock import patch

import pytest

import config_store
import proxy_manager as pm
from js_api import Api


class FakePsEnv:
    """In-memory stand-in for the HKCU\\Environment proxy values.

    Models the RAW per-name store the way the registry does — a missing key
    is a value that does not exist — so get_ps_env_snapshot sees all eight
    PS_ENV_NAMES spellings and a stash that misses one of them is visible to
    these tests instead of silently passing.
    """

    def __init__(self):
        self.vars = {}

    def snapshot(self):
        """What pm.get_ps_env_snapshot() would read out of this store."""
        return {n: (str(self.vars[n]) if self.vars.get(n) else None)
                for n in pm.PS_ENV_NAMES}

    def get_vars(self):
        # UI shape: three canonical keys, lowercase spelling preferred —
        # mirrors proxy_manager._ps_env_first((name.lower(), name)).
        out = {}
        for n in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            v = self.vars.get(n.lower()) or self.vars.get(n)
            out[n] = v if v else pm.UNSET
        return out

    def get_no_proxy(self):
        return self.vars.get("no_proxy") or self.vars.get("NO_PROXY") or ""

    def get_status(self):
        return self.vars.get("http_proxy") or self.vars.get("HTTP_PROXY") \
            or pm.UNSET

    def set_proxy(self, host, port, enable=True, scheme="http",
                  user="", password=""):
        url = pm.build_proxy_url(host, port, scheme, user, password)
        if enable:
            for n in pm.PS_ENV_URL_NAMES:
                self.vars[n] = url
            merged = pm._merged_no_proxy(self.get_no_proxy())
            for n in pm.PS_ENV_NO_PROXY_NAMES:
                self.vars[n] = merged
        else:
            removed = False
            for n in pm.PS_ENV_URL_NAMES:
                if self.vars.get(n) == url:
                    del self.vars[n]
                    removed = True
            if removed:
                kept = pm._stripped_no_proxy(self.get_no_proxy())
                for n in pm.PS_ENV_NO_PROXY_NAMES:
                    if kept:
                        self.vars[n] = kept
                    else:
                        self.vars.pop(n, None)
        return True

    def set_env_vars(self, values):
        # Faithful to pm.set_ps_env_vars: a name with a value is written,
        # EVERY other managed name — empty/None or not mentioned at all —
        # is deleted (an enable may have written a key the stash missed).
        for n in pm.PS_ENV_NAMES:
            v = values.get(n)
            if v:
                self.vars[n] = str(v)
            else:
                self.vars.pop(n, None)
        return True


class FakeSysProxy:
    """In-memory stand-in for the WinINET Internet Settings values."""

    def __init__(self):
        self.enabled = False
        self.server = ""
        self.override = ""

    def get_proxy(self):
        if not self.enabled:
            return pm.UNSET
        return pm._sys_proxy_server_to_url(self.server) or pm.UNSET

    def get_override(self):
        return self.override

    def set_proxy(self, host, port, enable=True, scheme="http",
                  user="", password=""):
        server = f"socks={host}:{port}" if str(scheme).startswith("socks") \
            else f"{host}:{port}"
        url = pm.build_proxy_url(host, port, scheme, user, password)
        if enable:
            self.enabled = True
            self.server = server
            entries = [e.strip() for e in self.override.split(";") if e.strip()]
            for e in ("localhost", "127.0.0.1", "<local>"):
                if e not in entries:
                    entries.append(e)
            self.override = ";".join(entries)
        else:
            cur_url = pm._sys_proxy_server_to_url(self.server) \
                if self.server else None
            if cur_url == url or self.server.strip() == server:
                self.enabled = False
                self.server = ""
                self.override = ";".join(
                    e.strip() for e in self.override.split(";")
                    if e.strip() and e.strip() not in pm._PROXY_OVERRIDE_OURS)
        return True

    def set_override(self, override):
        self.override = str(override or "")
        return True

    def get_server_raw(self):
        """ProxyServer read WITHOUT looking at ProxyEnable — the fake keeps
        the last server after a disable, exactly like Windows does when the
        user turns the proxy off in the Internet Options UI."""
        return self.server

    def set_server(self, server):
        """ProxyServer written verbatim, ProxyEnable untouched (mirrors
        pm.set_sys_proxy_server): '' clears the value, anything else lands
        as-is."""
        self.server = str(server or "")
        return True


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_restore_success_payload_has_note_key():
    from js_api import _mark_restored

    out = _mark_restored({"ok": True, "note": None})
    assert out["restored"] is True
    assert out["note_key"] == "restored"
    assert out["ok"] is True


def test_ps_reenable_after_port_change_disables_cleanly(tmp_path, monkeypatch):
    """Enable @7897 → edit port → enable @7890 → disable: the first disable
    must clear everything (the tool's own 7897 values are not user state and
    must not be stashed / re-armed by the restore path)."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakePsEnv()
    set_env_calls = []
    with patch("js_api.pm.get_ps_proxy_vars",
               side_effect=lambda: fake.get_vars()), \
         patch("js_api.pm.get_ps_env_snapshot", side_effect=fake.snapshot), \
         patch("js_api.pm.get_ps_no_proxy",
               side_effect=lambda: fake.get_no_proxy()), \
         patch("js_api.pm.get_ps_status",
               side_effect=lambda: fake.get_status()), \
         patch("js_api.pm.set_ps_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_ps_env_vars",
               side_effect=lambda v: set_env_calls.append(v) or True):
        assert api.do_toggle("ps", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("ps") is None       # user had nothing
        assert api.do_toggle("ps", True, "127.0.0.1", "7890")["ok"] is True
        assert api._restore.get("ps") is None       # our own 7897 not stashed
        r = api.do_toggle("ps", False)

    assert r["ok"] is True
    assert "restored" not in r                      # one disable, no "restore"
    assert set_env_calls == []                      # stash never written back
    assert fake.vars == {}                          # raw store, nothing left
    assert fake.get_no_proxy() == ""


def test_npm_reenable_after_port_change_does_not_stash_ours(tmp_path, monkeypatch):
    """Same contract as PowerShell: enable @7897 → enable @7890 → disable
    must not restore 7897 (it was this tool's leftover, not user state)."""
    api = _make_api(tmp_path, monkeypatch)
    current = {"url": None}

    def get_npm():
        return current["url"] or pm.UNSET

    def set_npm(host, port, enable=True, scheme="http", user="", password=""):
        url = pm.build_proxy_url(host, port, scheme, user, password)
        current["url"] = url if enable else None
        return True

    with patch("js_api.pm.get_npm_proxy", side_effect=get_npm), \
         patch("js_api.pm.set_npm_proxy", side_effect=set_npm):
        assert api.do_toggle("npm", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("npm") is None
        assert api.do_toggle("npm", True, "127.0.0.1", "7890")["ok"] is True
        assert api._restore.get("npm") is None
        r = api.do_toggle("npm", False)

    assert r["ok"] is True
    assert "restored" not in r
    assert current["url"] is None


def test_npmwsl_does_not_stash_nat_gateway_url(tmp_path, monkeypatch):
    """NAT mode writes the gateway IP; re-enable must not treat that URL as
    a user-configured proxy (disable used to 'restore' it, needing two offs)."""
    api = _make_api(tmp_path, monkeypatch)
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    gw_url = "http://172.24.0.1:7897"
    current = {"url": gw_url}

    with patch("js_api.pm.get_wsl_gateway_ip", return_value="172.24.0.1"), \
         patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
         patch("js_api.pm.get_npm_proxy_wsl",
               side_effect=lambda: current["url"] or pm.UNSET), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("npmwsl") is None


def test_sys_reenable_same_addr_strips_our_bypass_entries(tmp_path, monkeypatch):
    """Enable sys twice at the same address, then disable: the bypass list
    must not keep our merged entries (they used to be stashed and written
    back by the restore path, requiring a second disable)."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakeSysProxy()
    override_calls = []
    with patch("js_api.pm.get_sys_proxy",
               side_effect=lambda: fake.get_proxy()), \
         patch("js_api.pm.get_sys_proxy_override",
               side_effect=lambda: fake.get_override()), \
         patch("js_api.pm.get_sys_proxy_server_raw",
               side_effect=lambda: fake.get_server_raw()), \
         patch("js_api.pm.set_sys_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_sys_proxy_override",
               side_effect=lambda o: override_calls.append(o) or True), \
         patch("js_api.pm.set_sys_proxy_server", side_effect=fake.set_server):
        assert api.do_toggle("sys", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("sys") is None
        assert api.do_toggle("sys", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("sys") is None      # our own merge not stashed
        r = api.do_toggle("sys", False)

    assert r["ok"] is True
    assert "restored" not in r
    assert override_calls == []
    assert fake.enabled is False
    assert fake.server == ""
    assert fake.override == ""                      # no merged entries left


def test_sys_reenable_after_port_change_disables_cleanly(tmp_path, monkeypatch):
    """Same as the ps scenario for sys: re-enabling on a new port must not
    stash the old applied URL, so one disable turns the proxy off instead of
    "restoring" the previous address."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakeSysProxy()
    override_calls = []
    with patch("js_api.pm.get_sys_proxy",
               side_effect=lambda: fake.get_proxy()), \
         patch("js_api.pm.get_sys_proxy_override",
               side_effect=lambda: fake.get_override()), \
         patch("js_api.pm.get_sys_proxy_server_raw",
               side_effect=lambda: fake.get_server_raw()), \
         patch("js_api.pm.set_sys_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_sys_proxy_override",
               side_effect=lambda o: override_calls.append(o) or True), \
         patch("js_api.pm.set_sys_proxy_server", side_effect=fake.set_server):
        assert api.do_toggle("sys", True, "127.0.0.1", "7897")["ok"] is True
        assert api.do_toggle("sys", True, "127.0.0.1", "7890")["ok"] is True
        assert api._restore.get("sys") is None
        r = api.do_toggle("sys", False)

    assert r["ok"] is True
    assert "restored" not in r
    assert override_calls == []
    assert fake.enabled is False
    assert fake.server == ""


def test_do_toggle_setter_resolved_through_pm(tmp_path, monkeypatch):
    """The dispatch table stores pm setter NAMES: patching js_api.pm.set_ps_proxy
    must take effect (bound function objects silently bypassed such patches
    and would write the real registry)."""
    api = _make_api(tmp_path, monkeypatch)
    calls = []
    with patch("proxy_manager.winreg"), \
         patch("js_api.pm.get_ps_env_snapshot", return_value={}), \
         patch("js_api.pm.get_ps_proxy_vars",
               return_value={"HTTP_PROXY": pm.UNSET, "HTTPS_PROXY": pm.UNSET,
                             "ALL_PROXY": pm.UNSET}), \
         patch("js_api.pm.get_ps_no_proxy", return_value=""), \
         patch("js_api.pm.set_ps_proxy",
               side_effect=lambda *a, **k: calls.append(a) or True):
        r = api.do_toggle("ps", True, "127.0.0.1", "7897")

    assert r["ok"] is True
    assert calls == [("127.0.0.1", "7897", True)]
    assert api.applied_port == "7897"


def test_ps_stash_round_trips_both_spellings(tmp_path, monkeypatch):
    """Both spellings of every proxy env var are stashed per name, so one
    disable puts each one back verbatim. A stash built from the three
    canonical keys used to drop the lowercase names (the restore deleted
    them) and let HTTP_PROXY be overwritten by the lowercase value — the
    user's distinct uppercase URL was lost for good."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakePsEnv()
    initial = {"http_proxy": "http://corp-lc:8080",
               "HTTP_PROXY": "http://corp-uc:8080",
               "https_proxy": "http://corp-https:8080",
               "no_proxy": "example.com"}
    fake.vars = dict(initial)
    set_env_calls = []
    with patch("js_api.pm.get_ps_proxy_vars",
               side_effect=lambda: fake.get_vars()), \
         patch("js_api.pm.get_ps_env_snapshot", side_effect=fake.snapshot), \
         patch("js_api.pm.get_ps_no_proxy",
               side_effect=lambda: fake.get_no_proxy()), \
         patch("js_api.pm.get_ps_status",
               side_effect=lambda: fake.get_status()), \
         patch("js_api.pm.set_ps_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_ps_env_vars",
               side_effect=lambda v: set_env_calls.append(v)
               or fake.set_env_vars(v)):
        assert api.do_toggle("ps", True, "127.0.0.1", "7897")["ok"] is True

        env = api._restore["ps"]["env"]
        assert env["http_proxy"] == "http://corp-lc:8080"
        assert env["HTTP_PROXY"] == "http://corp-uc:8080"
        assert env["https_proxy"] == "http://corp-https:8080"
        assert env["no_proxy"] == "example.com"
        # The names the user never set must stay None, or the restore would
        # invent them (a NO_PROXY the user never had).
        assert env["HTTPS_PROXY"] is None
        assert env["all_proxy"] is None
        assert env["ALL_PROXY"] is None
        assert env["NO_PROXY"] is None

        assert api.do_toggle("ps", False)["ok"] is True

    assert len(set_env_calls) == 1
    assert fake.vars == initial     # exactly the user's four names, verbatim


def test_ps_restore_deletes_only_keys_enable_wrote(tmp_path, monkeypatch):
    """A restore must not resurrect a name the enable invented: only the
    names that existed before the enable come back, the ones the enable
    created stay deleted (they hold this tool's URL)."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakePsEnv()
    initial = {"http_proxy": "http://corp:8080",
               "HTTP_PROXY": "http://corp:8080"}
    fake.vars = dict(initial)
    with patch("js_api.pm.get_ps_proxy_vars",
               side_effect=lambda: fake.get_vars()), \
         patch("js_api.pm.get_ps_env_snapshot", side_effect=fake.snapshot), \
         patch("js_api.pm.get_ps_no_proxy",
               side_effect=lambda: fake.get_no_proxy()), \
         patch("js_api.pm.get_ps_status",
               side_effect=lambda: fake.get_status()), \
         patch("js_api.pm.set_ps_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_ps_env_vars",
               side_effect=lambda v: fake.set_env_vars(v)):
        assert api.do_toggle("ps", True, "127.0.0.1", "7897")["ok"] is True
        env = api._restore["ps"]["env"]
        assert env["http_proxy"] == "http://corp:8080"
        assert env["HTTP_PROXY"] == "http://corp:8080"
        assert api.do_toggle("ps", False)["ok"] is True

    assert fake.vars == initial
    for n in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        assert n not in fake.vars       # not resurrected by the restore


def test_ps_reenable_with_only_no_proxy_keeps_both_names(tmp_path,
                                                          monkeypatch):
    """The user set only the lowercase no_proxy. The stash judges each
    spelling on its own: no_proxy is the user's value, NO_PROXY was never
    set so it stays None (no fabricated uppercase name), and the disable
    leaves the user's no_proxy in place."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakePsEnv()
    fake.vars = {"no_proxy": "example.com"}
    with patch("js_api.pm.get_ps_proxy_vars",
               side_effect=lambda: fake.get_vars()), \
         patch("js_api.pm.get_ps_env_snapshot", side_effect=fake.snapshot), \
         patch("js_api.pm.get_ps_no_proxy",
               side_effect=lambda: fake.get_no_proxy()), \
         patch("js_api.pm.get_ps_status",
               side_effect=lambda: fake.get_status()), \
         patch("js_api.pm.set_ps_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_ps_env_vars",
               side_effect=lambda v: fake.set_env_vars(v)):
        assert api.do_toggle("ps", True, "127.0.0.1", "7897")["ok"] is True
        assert api.do_toggle("ps", True, "127.0.0.1", "7890")["ok"] is True
        env = api._restore["ps"]["env"]
        assert env["no_proxy"] == "example.com"
        assert env["NO_PROXY"] is None
        assert api.do_toggle("ps", False)["ok"] is True

    assert fake.get_no_proxy() == "example.com"
    assert fake.vars == {"no_proxy": "example.com"}


def test_ps_stash_after_user_restored_our_url_writes_both_no_proxy_names(
        tmp_path, monkeypatch):
    """Our enable merged into BOTH NO_PROXY spellings; after the user put
    our URL back by hand and left only no_proxy, the next stash must strip
    our merged entries per spelling (they were ours) while keeping the
    user's no_proxy — and must not fabricate a NO_PROXY."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakePsEnv()
    fake.vars = {"no_proxy": "example.com"}
    applied = "http://127.0.0.1:7897"
    with patch("js_api.pm.get_ps_proxy_vars",
               side_effect=lambda: fake.get_vars()), \
         patch("js_api.pm.get_ps_env_snapshot", side_effect=fake.snapshot), \
         patch("js_api.pm.get_ps_no_proxy",
               side_effect=lambda: fake.get_no_proxy()), \
         patch("js_api.pm.get_ps_status",
               side_effect=lambda: fake.get_status()), \
         patch("js_api.pm.set_ps_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_ps_env_vars",
               side_effect=lambda v: fake.set_env_vars(v)):
        assert api.do_toggle("ps", True, "127.0.0.1", "7897")["ok"] is True
        assert api.do_toggle("ps", False)["ok"] is True   # stash popped
        assert api._restore.get("ps") is None

        # The user hand-restores our URL and keeps only their no_proxy.
        fake.vars = {"http_proxy": applied, "no_proxy": "example.com"}
        assert api.do_toggle("ps", True, "127.0.0.1", "7890")["ok"] is True
        env = api._restore["ps"]["env"]
        assert env["no_proxy"] == "example.com"
        assert env["NO_PROXY"] is None
        assert env["http_proxy"] is None      # our own URL, not user state
        assert api.do_toggle("ps", False)["ok"] is True

    assert fake.get_no_proxy() == "example.com"
    assert fake.vars == {"no_proxy": "example.com"}


@pytest.mark.parametrize("bad", [{}, {"env": {}}, {"url": "http://127.0.0.1:9"}])
def test_corrupt_ps_stash_kept_and_reported(tmp_path, monkeypatch, bad):
    """A dict stash without a non-empty "env" is corruption: report it and
    keep the stash instead of "restoring" nothing (which used to succeed,
    pop the stash and lose the user's original values)."""
    api = _make_api(tmp_path, monkeypatch)
    api._restore["ps"] = dict(bad)
    set_env = []
    with patch("js_api.pm.set_ps_env_vars",
               side_effect=lambda v: set_env.append(v) or True):
        r = api.do_toggle("ps", False)

    assert r.get("ok") is not True
    assert "暂存数据不完整" in r.get("error", "")
    assert api._restore.get("ps") == dict(bad)      # kept, not popped
    assert set_env == []                            # nothing was written


def test_failed_enable_keeps_stash_and_keeps_form_address(
        tmp_path, monkeypatch):
    """A failed enable must keep the stash of the user's URL: a mid-write
    failure may already have overwritten some values (proxy_manager rolls
    those back to the originals), so dropping the stash would leave the
    user's original config unrecoverable. It must not retarget
    applied_*/host either."""
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    with patch("js_api.pm.get_npm_proxy",
               return_value="http://corp:8080"), \
         patch("js_api.pm.set_npm_proxy", return_value="disk full"):
        r = api.do_toggle("npm", True, "127.0.0.1", "1080")
    assert r.get("ok") is not True
    assert api._restore.get("npm") == "http://corp:8080"
    assert api.applied_port == "7897"
    assert api.port == "7897"
