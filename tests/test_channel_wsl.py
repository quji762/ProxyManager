"""Regression: test_channel for WSL sources must call wfn() only once and
check HTTPS_PROXY (not just HTTP_PROXY + ALL_PROXY)."""

from unittest.mock import patch

import config_store
from js_api import Api


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_wsl_channel_checks_https_proxy(tmp_path, monkeypatch):
    """When only HTTPS_PROXY is set in WSL, test_channel('wsl_https') must
    find the URL and attempt a probe (not immediately fail with '未设置')."""
    api = _make_api(tmp_path, monkeypatch)

    def fake_wsl_vars():
        return {"HTTP_PROXY": "未设置", "HTTPS_PROXY": "http://172.24.0.1:7897",
                "ALL_PROXY": "未设置"}

    with patch("js_api.pm.get_wsl_proxy_vars", side_effect=fake_wsl_vars), \
         patch.object(api, "_wsl_proxy_probe",
                      return_value={"ok": True, "ms": 42}):
        r = api.test_channel("wsl_https")
    # Before the fix, this returned "未设置代理" because only HTTP_PROXY and
    # ALL_PROXY were checked.  After the fix, HTTPS_PROXY is found and the
    # probe is called — we mocked it to return ok=True.
    assert r.get("ok") is True, f"Expected ok=True but got {r}"


def test_wsl_channel_single_subprocess_call(tmp_path, monkeypatch):
    """Regression: test_channel('wsl') must call get_wsl_proxy_vars exactly
    once (the old code called it twice, doubling WSL latency)."""
    api = _make_api(tmp_path, monkeypatch)
    call_count = {"n": 0}

    def counting_wsl_vars():
        call_count["n"] += 1
        return {"HTTP_PROXY": "http://172.24.0.1:7897",
                "HTTPS_PROXY": "http://172.24.0.1:7897",
                "ALL_PROXY": "http://172.24.0.1:7897"}

    with patch("js_api.pm.get_wsl_proxy_vars", side_effect=counting_wsl_vars), \
         patch.object(api, "_wsl_proxy_probe",
                      return_value={"ok": True, "ms": 42}):
        api.test_channel("wsl")

    assert call_count["n"] == 1, (
        f"get_wsl_proxy_vars called {call_count['n']} times; expected 1")
