"""Restore-path hardening regressions (security audit R2).

- An undecryptable ps env stash must be dropped (stash_dropped) and the
  surgical disable left to run — never delete all eight env names blindly.
- The sys restore path must validate the stashed URL before writing
  ProxyServer (parse_proxy_url is laxer than valid_host).
- Background images must be local files: UNC/device paths are rejected so
  reading one cannot trigger SMB authentication (NTLM-relay surface).
"""

from unittest.mock import patch

import config_store
from js_api import Api, _is_network_path


def _api():
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_ps_stash_undecryptable_drops_not_deletes():
    api = _api()
    prev = {"env": {"HTTP_PROXY": "DPAPI:AAAA", "HTTPS_PROXY": None}}
    api._restore["ps"] = {"env": dict(prev["env"])}
    with patch("js_api.pm.set_ps_env_vars") as fake_set:
        r = api._restore_stashed("ps", prev)
    assert r.get("stash_dropped") is True
    assert r.get("error_key") == "errStashDroppedDpapi"
    fake_set.assert_not_called()          # 绝不把全部环境变量名删光
    assert "ps" not in api._restore       # 暂存已移除


def test_ps_stash_clean_env_still_restores():
    api = _api()
    prev = {"env": {"HTTP_PROXY": "http://corp:8080", "HTTPS_PROXY": None}}
    with patch("js_api.pm.set_ps_env_vars", return_value=True) as fake_set:
        r = api._restore_stashed("ps", prev)
    assert r.get("ok") is True
    fake_set.assert_called_once_with(prev["env"])


def test_sys_stash_invalid_host_not_written():
    api = _api()
    # parse_proxy_url 接受 ho'st，但 valid_host 拒绝：绝不能写进 ProxyServer。
    prev = {"url": "http://ho'st:7890", "override": "localhost", "server": None}
    with patch("js_api.pm.set_sys_proxy") as fake_set, \
         patch.object(api, "do_toggle", return_value={"ok": True}), \
         patch("js_api.pm.set_sys_proxy_server", return_value=True), \
         patch("js_api.pm.set_sys_proxy_override", return_value=True):
        r = api._restore_stashed("sys", prev)
    fake_set.assert_not_called()
    assert r.get("ok") is True


def test_sys_stash_valid_host_restores():
    api = _api()
    prev = {"url": "http://corp:8080", "override": "", "server": None}
    with patch("js_api.pm.set_sys_proxy", return_value=True) as fake_set, \
         patch("js_api.pm.set_sys_proxy_override", return_value=True):
        r = api._restore_stashed("sys", prev)
    assert r.get("ok") is True
    fake_set.assert_called_once()
    assert fake_set.call_args[0][:2] == ("corp", "8080")


def test_is_network_path():
    assert _is_network_path("\\\\host\\share\\a.png")
    assert _is_network_path("\\\\?\\C:\\a.png")
    assert _is_network_path("\\\\.\\pipe\\x")
    assert _is_network_path("//host/a.png")
    assert not _is_network_path("C:\\Users\\me\\a.png")
    assert not _is_network_path("/home/me/a.png")
    assert not _is_network_path("a.png")


def test_save_config_rejects_unc_background():
    api = _api()
    # UNC 路径必须在 os.path.isfile 之前被拒（短路），不触发 SMB 认证。
    with patch("js_api.os.path.isfile") as fake_isfile:
        assert api.save_config({"bg_path": "\\\\host\\share\\a.png"}) is True
    fake_isfile.assert_not_called()
    assert config_store.load_config().get("bg_path", "") == ""


def test_get_background_rejects_unc_without_reading():
    api = _api()
    with patch("js_api.load_config",
               return_value={"bg_path": "\\\\host\\share\\a.png"}), \
         patch("js_api.os.path.isfile") as fake_isfile:
        assert api.get_background() is None
    fake_isfile.assert_not_called()