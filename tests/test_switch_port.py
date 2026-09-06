"""switch_port: only endpoints matching the managed URL are rewritten."""

from unittest.mock import patch

import config_store
from js_api import Api


def test_switch_port_noop_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    r = api.switch_port("7897", "127.0.0.1")
    assert r["ok"] is True
    assert r["applied"] == []


def test_switch_port_rewrites_only_ours(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme = "http"
    managed = "http://127.0.0.1:7897"
    calls = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return True
        return _fn

    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value="http://other:9"), \
         patch("js_api.pm.get_git_proxy_win", return_value=managed), \
         patch("js_api.pm.get_npm_proxy", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value="未设置"), \
         patch.object(api, "refresh_item", return_value={"on": False, "git": "未设置"}), \
         patch("js_api.pm.set_ps_proxy", side_effect=record("ps")), \
         patch("js_api.pm.set_sys_proxy", side_effect=record("sys")), \
         patch("js_api.pm.set_git_proxy", side_effect=record("git")), \
         patch("js_api.pm.set_wsl_proxy", side_effect=record("wsl")), \
         patch("js_api.pm.set_npm_proxy", side_effect=record("npm")), \
         patch("js_api.pm.set_pip_proxy", side_effect=record("pip")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=record("npmwsl")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=record("pipwsl")), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"):
        r = api.switch_port("7890", "127.0.0.1")

    names = [c[0] for c in calls]
    assert r["ok"] is True
    assert "ps" in names
    assert "git" in names
    assert "sys" not in names  # foreign URL must survive
    assert api.port == "7890"


def test_switch_port_migrates_after_unapplied_save_config(tmp_path, monkeypatch):
    """Regression: persistSoon saves the not-yet-applied form address via
    save_config, which used to poison the cache switch_port compares against
    — endpoints stayed on the old port while the UI reported success.
    switch_port must match against the last APPLIED address instead."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    # The user typed 7890 into the form and persistSoon already saved it.
    assert api.save_config({"host": "127.0.0.1", "port": "7890"}) is True
    assert api.port == "7890"
    managed = "http://127.0.0.1:7897"
    calls = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return True
        return _fn

    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value="http://other:9"), \
         patch("js_api.pm.get_git_proxy_win", return_value=managed), \
         patch("js_api.pm.get_npm_proxy", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value="未设置"), \
         patch.object(api, "refresh_item", return_value={"on": False, "git": "未设置"}), \
         patch("js_api.pm.set_ps_proxy", side_effect=record("ps")), \
         patch("js_api.pm.set_sys_proxy", side_effect=record("sys")), \
         patch("js_api.pm.set_git_proxy", side_effect=record("git")), \
         patch("js_api.pm.set_wsl_proxy", side_effect=record("wsl")), \
         patch("js_api.pm.set_npm_proxy", side_effect=record("npm")), \
         patch("js_api.pm.set_pip_proxy", side_effect=record("pip")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=record("npmwsl")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=record("pipwsl")), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"):
        r = api.switch_port("7890", "127.0.0.1")

    names = [c[0] for c in calls]
    assert r["ok"] is True
    assert "ps" in names       # endpoint on the OLD applied port must migrate
    assert "git" in names
    assert api.port == "7890"
    assert api.applied_port == "7890"
