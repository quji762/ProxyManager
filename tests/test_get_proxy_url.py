"""Api.get_proxy_url: canonical URL for the copy button."""

from unittest.mock import patch

import config_store
import proxy_manager as pm
from js_api import Api


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_builds_canonical_url_with_percent_encoded_credentials(
        tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    r = api.get_proxy_url("127.0.0.1", "7890", "socks5", "user", "p@ss")
    assert r["ok"] is True
    assert r["url"] == pm.build_proxy_url(
        "127.0.0.1", "7890", scheme="socks5", user="user", password="p@ss")
    assert "p%40ss" in r["url"]          # userinfo encoded, never raw '@'
    assert r["url"].count("@") == 1


def test_invalid_form_address_returns_localized_error(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    r = api.get_proxy_url("bad host", "7890")
    assert r["ok"] is False
    assert r["error_key"] == "errBadAddr"


def test_falls_back_to_cached_form_address(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.host, api.port, api.scheme = "127.0.0.1", "7897", "http"
    api.user = api.password = ""
    r = api.get_proxy_url()
    assert r["url"] == "http://127.0.0.1:7897"
