"""Api.get_diagnostics: non-sensitive, copyable diagnostics."""

from unittest.mock import patch

import config_store
from js_api import Api


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_diagnostics_excludes_host_userinfo_and_password(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.applied_host, api.applied_port = "10.0.0.5", "7897"
    api.applied_kw = {"scheme": "http", "user": "secretuser",
                      "password": "secretpw"}
    with patch("js_api.pm.wsl_available", return_value=True):
        r = api.get_diagnostics()
    assert r["ok"] is True
    blob = r["text"] + str(r["data"])
    assert "10.0.0.5" not in blob
    assert "secretuser" not in blob
    assert "secretpw" not in blob
    assert r["data"]["applied"] is True
    assert r["data"]["applied_port"] == "7897"
    assert r["data"]["has_credentials"] is True
    assert "ProxyManager" in r["text"]


def test_diagnostics_reports_switches_and_residual(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.applied_host = ""
    api._residual = {"pip": {"host": "127.0.0.1", "port": "7890",
                             "scheme": "http", "user": "", "password": ""}}
    with patch("js_api.pm.wsl_available", return_value=False), \
         patch("js_api.autostart_enabled", return_value=False):
        r = api.get_diagnostics()
    assert r["data"]["applied"] is False
    assert r["data"]["residual_targets"] == ["pip"]
    assert "residual: pip" in r["text"]
    assert set(r["data"]["toggles"]) == {
        "powershell", "wsl", "git_win", "git_wsl",
        "npm", "pip", "npmwsl", "pipwsl"}
