"""Verbatim restore regressions (security audit R3, finding #7).

A string stash stores the user's original proxy URL exactly as detected
(_detected_url returns the raw string). Restore must write that exact value
back — not re-build it through build_proxy_url, which normalizes https→http
and socks4→socks5 and drops any path. Degrading the user's original proxy on
restore violates "put the exact pre-enable value back".
"""

from unittest.mock import patch

import proxy_manager as pm
from js_api import Api


def _api():
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def _enable_then_disable_npm(npmrc, original):
    npmrc.write_text(f"proxy = {original}\n", encoding="utf-8")
    api = _api()
    assert api.do_toggle("npm", True, "127.0.0.1", "7897")["ok"] is True
    assert pm.get_npm_proxy() == "http://127.0.0.1:7897"
    r = api.do_toggle("npm", False)
    assert r["ok"] is True
    return pm.get_npm_proxy()


def test_npm_restore_preserves_https_scheme(tmp_path, monkeypatch):
    npmrc = tmp_path / ".npmrc"
    monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))
    assert _enable_then_disable_npm(npmrc, "https://proxy:8080") \
        == "https://proxy:8080"


def test_npm_restore_preserves_socks4_scheme(tmp_path, monkeypatch):
    npmrc = tmp_path / ".npmrc"
    monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))
    assert _enable_then_disable_npm(npmrc, "socks4://proxy:1080") \
        == "socks4://proxy:1080"


def test_npm_restore_preserves_path(tmp_path, monkeypatch):
    npmrc = tmp_path / ".npmrc"
    monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))
    assert _enable_then_disable_npm(npmrc, "http://proxy:8080/pac") \
        == "http://proxy:8080/pac"


def test_pip_restore_preserves_https_scheme(tmp_path, monkeypatch):
    ini = tmp_path / "pip.ini"
    monkeypatch.setattr(pm, "_pip_config_path", lambda: str(ini))
    ini.write_text("[global]\nproxy = https://proxy:8080\n", encoding="utf-8")
    api = _api()
    assert api.do_toggle("pip", True, "127.0.0.1", "7897")["ok"] is True
    assert api.do_toggle("pip", False)["ok"] is True
    assert pm.get_pip_proxy() == "https://proxy:8080"