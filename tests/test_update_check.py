# -*- coding: utf-8 -*-
"""Update check: GitHub API response parsing and version comparison.

The HTTP call itself is mocked — no network in tests.
"""

from unittest.mock import patch, MagicMock

from js_api import Api


def _payload(tag):
    resp = MagicMock()
    resp.read.return_value = ('{"tag_name": "%s", "html_url": "https://x"}' % tag).encode()
    resp.__enter__.return_value = resp
    return resp


def setup_function(_):
    Api._UPDATE_CACHE = None


def teardown_function(_):
    Api._UPDATE_CACHE = None


def _api():
    api = Api.__new__(Api)  # skip __init__ (threads, config)
    return api


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
