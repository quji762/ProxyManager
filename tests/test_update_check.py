"""PAC refresh stays a read-only probe and does not change the sys shape.

The in-app update check was removed; these cases used to share its file.
"""

from unittest.mock import patch

from js_api import Api


def _api():
    return Api.__new__(Api)  # skip __init__ (threads, config)


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
