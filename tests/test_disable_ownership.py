"""Disable ownership regressions (security audit R3, finding #4).

The surgical setters (set_ps_proxy / _ini_edit for npm/pip) only remove a
value EQUAL to the URL they are handed. _do_toggle's disable branch used to
rebuild that URL from the *currently detected* value, so the guard always
matched and a proxy the user set by hand after our enable was deleted on
disable — a core-invariant violation (disable must remove only our value).

These tests drive the real setters against real temp files end-to-end:
enable through do_toggle, let the user edit the file, then disable and
assert the user's value survives.
"""

from unittest.mock import patch

import proxy_manager as pm
from js_api import Api


def _api():
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


OURS = "http://127.0.0.1:7897"
USER = "http://user-own:9999"


def test_npm_disable_preserves_user_changed_value(tmp_path, monkeypatch):
    npmrc = tmp_path / ".npmrc"
    monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))
    api = _api()

    assert api.do_toggle("npm", True, "127.0.0.1", "7897")["ok"] is True
    assert pm.get_npm_proxy() == OURS
    assert api.applied_port == "7897"

    # User replaces our proxy with their own by hand.
    npmrc.write_text(f"proxy = {USER}\n", encoding="utf-8")
    assert pm.get_npm_proxy() == USER

    r = api.do_toggle("npm", False)
    assert r["ok"] is True
    # Our disable must NOT delete the user's proxy.
    assert pm.get_npm_proxy() == USER


def test_pip_disable_preserves_user_changed_value(tmp_path, monkeypatch):
    ini = tmp_path / "pip.ini"
    monkeypatch.setattr(pm, "_pip_config_path", lambda: str(ini))
    api = _api()

    assert api.do_toggle("pip", True, "127.0.0.1", "7897")["ok"] is True
    assert pm.get_pip_proxy() == OURS

    ini.write_text(f"[global]\nproxy = {USER}\n", encoding="utf-8")
    assert pm.get_pip_proxy() == USER

    r = api.do_toggle("pip", False)
    assert r["ok"] is True
    assert pm.get_pip_proxy() == USER


def test_npm_disable_still_removes_our_value(tmp_path, monkeypatch):
    """Normal disable (value unchanged) must still clear our proxy."""
    npmrc = tmp_path / ".npmrc"
    monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))
    api = _api()

    assert api.do_toggle("npm", True, "127.0.0.1", "7897")["ok"] is True
    assert pm.get_npm_proxy() == OURS

    r = api.do_toggle("npm", False)
    assert r["ok"] is True
    assert pm.get_npm_proxy() == pm.UNSET


def test_ps_disable_hands_setter_managed_url_not_detected():
    """ps is surgical too (set_ps_proxy deletes only values == url). The
    disable must hand the setter the URL WE wrote, not the detected one, or
    a user-changed env value would be deleted. Spy on the setter argument."""
    api = _api()
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    seen = []

    def spy(host, port, enable=True, **kw):
        seen.append((host, str(port), enable, kw))
        return True

    with patch("js_api.pm.get_ps_status", return_value=USER), \
            patch("js_api.pm.set_ps_proxy", side_effect=spy):
        r = api.do_toggle("ps", False)

    assert r["ok"] is True
    assert seen == [("127.0.0.1", "7897", False,
                     {"scheme": "http", "user": "", "password": ""})]