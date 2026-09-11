"""Stash/restore regression tests: this tool must never stash (and later
"restore") proxy values it wrote itself, and a corrupted stash must not be
silently "restored" as an empty state."""

from unittest.mock import patch

import pytest

import config_store
import proxy_manager as pm
from js_api import Api


class FakePsEnv:
    """In-memory stand-in for the HKCU\\Environment proxy values."""

    def __init__(self):
        self.vars = {"HTTP_PROXY": pm.UNSET, "HTTPS_PROXY": pm.UNSET,
                     "ALL_PROXY": pm.UNSET}
        self.no_proxy = ""

    def get_vars(self):
        return dict(self.vars)

    def get_no_proxy(self):
        return self.no_proxy

    def get_status(self):
        return self.vars["HTTP_PROXY"]

    def set_proxy(self, host, port, enable=True, scheme="http",
                  user="", password=""):
        url = pm.build_proxy_url(host, port, scheme, user, password)
        if enable:
            for n in self.vars:
                self.vars[n] = url
            self.no_proxy = pm._merged_no_proxy(self.no_proxy)
        else:
            removed = False
            for n, v in self.vars.items():
                if v == url:
                    self.vars[n] = pm.UNSET
                    removed = True
            if removed:
                self.no_proxy = pm._stripped_no_proxy(self.no_proxy)
        return True

    def set_env_vars(self, values):
        for n, v in values.items():
            if n == "NO_PROXY":
                self.no_proxy = str(v) if v else ""
            elif v:
                self.vars[n] = str(v)
            else:
                self.vars[n] = pm.UNSET
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


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_ps_reenable_after_port_change_disables_cleanly(tmp_path, monkeypatch):
    """Enable @7897 → edit port → enable @7890 → disable: the first disable
    must clear everything (the tool's own 7897 values are not user state and
    must not be stashed / re-armed by the restore path)."""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakePsEnv()
    set_env_calls = []
    with patch("js_api.pm.get_ps_proxy_vars",
               side_effect=lambda: fake.get_vars()), \
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
    assert all(v == pm.UNSET for v in fake.vars.values())
    assert fake.no_proxy == ""


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
         patch("js_api.pm.set_sys_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_sys_proxy_override",
               side_effect=lambda o: override_calls.append(o) or True):
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
         patch("js_api.pm.set_sys_proxy", side_effect=fake.set_proxy), \
         patch("js_api.pm.set_sys_proxy_override",
               side_effect=lambda o: override_calls.append(o) or True):
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
