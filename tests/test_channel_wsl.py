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


def test_ps_all_chip_probes_all_proxy_not_http(tmp_path, monkeypatch):
    """Regression: the ALL_PROXY row's test chip used data-test="ps", which
    probes HTTP_PROXY. The dedicated ps_all source must read ALL_PROXY and
    probe through THAT value."""
    api = _make_api(tmp_path, monkeypatch)
    probed = {}

    def fake_probe(proxy_url, parsed, _t, _urlreq):
        probed["url"] = proxy_url
        return {"ok": True, "ms": 7}

    with patch("js_api.pm.get_ps_proxy_vars",
               return_value={"HTTP_PROXY": "http://10.0.0.1:1111",
                             "HTTPS_PROXY": "http://10.0.0.1:2222",
                             "ALL_PROXY": "http://10.0.0.1:3333"}), \
         patch.object(api, "_proxy_probe", side_effect=fake_probe):
        r = api.test_channel("ps_all")

    assert r.get("ok") is True
    assert probed["url"] == "http://10.0.0.1:3333"


def test_wsl_all_chip_probes_all_proxy(tmp_path, monkeypatch):
    """Same as ps_all for the WSL side: wsl_all reads ALL_PROXY from the
    WSL block and probes through it."""
    api = _make_api(tmp_path, monkeypatch)
    probed = {}

    def fake_wsl_probe(proxy_url, parsed):
        probed["url"] = proxy_url
        return {"ok": True, "ms": 9}

    with patch("js_api.pm.get_wsl_proxy_vars",
               return_value={"HTTP_PROXY": "未设置",
                             "HTTPS_PROXY": "未设置",
                             "ALL_PROXY": "http://172.24.0.1:7899"}), \
         patch.object(api, "_wsl_proxy_probe", side_effect=fake_wsl_probe):
        r = api.test_channel("wsl_all")

    assert r.get("ok") is True
    assert probed["url"] == "http://172.24.0.1:7899"


def test_ps_all_unset_reports_not_set(tmp_path, monkeypatch):
    with patch("js_api.pm.get_ps_proxy_vars",
               return_value={"HTTP_PROXY": "http://10.0.0.1:1111",
                             "HTTPS_PROXY": "未设置",
                             "ALL_PROXY": "未设置"}):
        api = _make_api(tmp_path, monkeypatch)
        r = api.test_channel("ps_all")
    assert r.get("ok") is False
    assert "未设置" in r.get("error", "")


def test_http_probe_err_maps_fail_token():
    from js_api import Api
    assert Api._http_probe_err("FAIL") == "连接失败"
    assert Api._http_probe_err("000") == "连接失败"
    assert Api._http_probe_err("0") == "连接失败"
    assert Api._http_probe_err("") == "连接失败"
    assert Api._http_probe_err("503") == "HTTP 503"
    assert Api._http_probe_err("204") == "HTTP 204"


def test_wsl_proxy_probe_fail_token_is_not_http_status(tmp_path, monkeypatch):
    """The bash loop echoes FAIL when every URL misses — must not surface
    as 'HTTP FAIL' on the test chip."""
    api = _make_api(tmp_path, monkeypatch)

    class _R:
        stdout = "FAIL\n"
        returncode = 0

    with patch("js_api.subprocess.run", return_value=_R()), \
            patch("js_api.pm.wsl_available", return_value=True):
        r = api._wsl_proxy_probe("http://127.0.0.1:7897", {})
    assert r.get("ok") is False
    assert r.get("error") == "连接失败"
    assert "FAIL" not in r.get("error", "")
