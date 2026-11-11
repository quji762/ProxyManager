"""Update check: GitHub API response parsing and version comparison.

The HTTP call itself is mocked — no network in tests.
"""

from unittest.mock import MagicMock, patch

from js_api import Api


def _payload(tag):
    resp = MagicMock()
    resp.read.return_value = f'{{"tag_name": "{tag}", "html_url": "https://x"}}'.encode()
    resp.__enter__.return_value = resp
    return resp


def setup_function(_):
    Api._UPDATE_CACHE = None


def teardown_function(_):
    Api._UPDATE_CACHE = None


def _api():
    return Api.__new__(Api)  # skip __init__ (threads, config)


def test_update_available_when_latest_is_newer():
    api = _api()
    # check_update does `import urllib.request as _urlreq` inside the function
    # body, so urllib.request.urlopen IS the real entry point — the mock is
    # asserted on below so this cannot rot back into a dead patch.
    with patch.object(Api, "_GITHUB_LATEST", "http://x"), \
         patch("urllib.request.urlopen", return_value=_payload("v1.5.0")) as m:
        r = api.check_update()
    assert m.call_count == 1
    assert r["status"] == "update"
    assert r["latest"] == "1.5.0"


def test_latest_when_current_is_newest():
    api = _api()
    with patch("urllib.request.urlopen", return_value=_payload("v1.4.0")):
        r = api.check_update()
    assert r["status"] == "latest"


def test_prerelease_tag_ignored_comparison():
    """A tag with a suffix compares only its numeric prefix pieces."""
    api = _api()
    with patch("urllib.request.urlopen", return_value=_payload("v1.4.0-beta.1")):
        r = api.check_update()
    # numeric prefix (1, 4) == current (1, 4) → not an update
    assert r["status"] == "latest"


def test_error_on_network_failure_is_silent_payload():
    api = _api()
    with patch("urllib.request.urlopen", side_effect=OSError("offline")):
        r = api.check_update()
    assert r["status"] == "error"
    assert "offline" in r["error"]


def test_result_cached_for_repeated_calls():
    api = _api()
    with patch("urllib.request.urlopen", return_value=_payload("v1.5.0")) as m:
        api.check_update()
        api.check_update()
    assert m.call_count == 1


# ── open_release_page: fixed destination, no open-redirect surface ──────────
_RELEASES = "https://github.com/quji762/ProxyManager/releases"


def test_open_release_page_takes_no_url_argument():
    import inspect
    params = list(inspect.signature(Api.open_release_page).parameters)
    assert params == ["self"], (
        "open_release_page must not accept a caller-supplied URL — that "
        "would turn the bridge into an open redirect")


def test_open_release_page_uses_fixed_url_without_cache():
    api = _api()
    with patch("webbrowser.open", return_value=True) as m:
        assert api.open_release_page() is True
    m.assert_called_once_with(_RELEASES)


def test_open_release_page_prefers_cached_repo_url():
    api = _api()
    tag_url = _RELEASES + "/tag/v9.9.9"
    Api._UPDATE_CACHE = (0.0, {"status": "update", "url": tag_url})
    with patch("webbrowser.open", return_value=True) as m:
        api.open_release_page()
    m.assert_called_once_with(tag_url)


def test_open_release_page_rejects_foreign_cached_url():
    api = _api()
    for bad in ("https://evil.example/phish",
                "https://github.com/quji762/ProxyManager.evil/x",
                "http://github.com/quji762/ProxyManager/releases"):
        Api._UPDATE_CACHE = (0.0, {"status": "update", "url": bad})
        with patch("webbrowser.open", return_value=True) as m:
            api.open_release_page()
        m.assert_called_once_with(_RELEASES)


def test_open_release_page_returns_false_on_browser_failure():
    api = _api()
    with patch("webbrowser.open", side_effect=OSError("no browser")), \
         patch("js_api.pm.log_exception") as log:
        assert api.open_release_page() is False
    log.assert_called_once()


# ── refresh_item("pac"): read-only PAC awareness, sys shape unchanged ───────
def test_refresh_item_pac_returns_extended_sys_shape():
    api = _api()
    ex = {"url": "127.0.0.1:7890",
          "autoconfig_url": "http://wpad/wpad.dat", "pac": True}
    with patch("js_api.pm.get_sys_proxy_ex", return_value=ex) as m:
        assert api.refresh_item("pac") == ex
    m.assert_called_once_with()


def test_refresh_item_sys_shape_unchanged():
    """The sys probe must keep returning a plain string — the endpoint
    list and the test chip consume it positionally."""
    api = _api()
    with patch("js_api.pm.get_sys_proxy", return_value="127.0.0.1:7890"):
        r = api.refresh_item("sys")
    assert r == "127.0.0.1:7890"
    assert isinstance(r, str)
