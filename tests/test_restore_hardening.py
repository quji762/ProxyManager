"""Restore-path hardening regressions (security audit R2).

- An undecryptable ps env stash must be dropped (stash_dropped) and the
  surgical disable left to run — never delete all eight env names blindly.
- The sys restore path must validate the stashed URL before writing
  ProxyServer (parse_proxy_url is laxer than valid_host).
- Background images must be local files: UNC/device paths are rejected so
  reading one cannot trigger SMB authentication (NTLM-relay surface).
  A local junction or relative symlink that resolves to a share is rejected
  the same way, and the check must not follow the reparse point.
"""

from unittest.mock import patch

import config_store
from js_api import (
    _REPARSE_MOUNT_POINT,
    _REPARSE_SYMLINK,
    Api,
    _bg_path_acceptable,
    _is_network_path,
    _path_crosses_reparse,
)


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


def test_reparse_point_is_rejected_before_follow():
    """A local path whose component is a junction/symlink must not be opened.
    os.path.isfile follows reparse points and would authenticate to a UNC target."""
    api = _api()
    local = "C:\\Users\\me\\link\\a.png"

    def attrs(path):
        if path.rstrip("\\").endswith("link"):
            return 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
        return 0x10  # directory

    with patch("js_api._get_file_attributes", side_effect=attrs), \
         patch("js_api.os.path.isfile") as fake_isfile:
        assert _path_crosses_reparse(local) is True
        assert _bg_path_acceptable(local) is False
        assert api.save_config({"bg_path": local}) is True
    fake_isfile.assert_not_called()
    assert config_store.load_config().get("bg_path", "") == ""


def _reparse_buffer(tag, substitute):
    """Minimal mount-point or symlink reparse buffer.

    SubstituteNameOffset is relative to the path buffer (16 for a junction,
    20 for a symlink). Print name is empty."""
    name = substitute.encode("utf-16le")
    names = (0).to_bytes(2, "little") + len(name).to_bytes(2, "little")
    names += len(name).to_bytes(2, "little") + (0).to_bytes(2, "little")
    # Symlink path buffer starts 4 bytes later (the Flags field).
    flags = (1).to_bytes(4, "little") if tag == _REPARSE_SYMLINK else b""
    body = names + flags + name
    return (tag.to_bytes(4, "little") + len(body).to_bytes(2, "little")
            + (0).to_bytes(2, "little") + body)


def test_local_junction_is_followed_only_on_the_resolved_path():
    """A junction to another drive is local. The child must be looked up on
    the substitute path, not by following the link with GetFileAttributes."""
    local = "C:\\Users\\me\\link\\a.png"
    seen = []

    def attrs(path):
        seen.append(path)
        if path.rstrip("\\").endswith("link"):
            return 0x400
        if path.rstrip("\\").endswith("a.png"):
            return 0x20
        return 0x10

    def read(path):
        if path.rstrip("\\").endswith("link"):
            return _reparse_buffer(_REPARSE_MOUNT_POINT, "\\??\\D:\\photos")
        return None

    with patch("js_api._get_file_attributes", side_effect=attrs), \
         patch("js_api._read_reparse", side_effect=read), \
         patch("js_api.os.path.isfile", return_value=True) as fake_isfile:
        assert _path_crosses_reparse(local) is False
        assert _bg_path_acceptable(local) is True
    assert "C:\\Users\\me\\link\\a.png" not in seen
    assert "D:\\photos" in seen
    assert "D:\\photos\\a.png" in seen
    fake_isfile.assert_called_once()


def test_relative_symlink_to_unc_junction_is_rejected_before_follow():
    """`link` → `..\\evil`, and `evil` is a UNC junction. The child of either
    reparse point must not be queried: that lookup would follow onto the share."""
    local = "C:\\Users\\me\\link\\a.png"
    seen = []

    def attrs(path):
        seen.append(path)
        assert not path.startswith("\\\\")
        assert not path.rstrip("\\").endswith("a.png")
        if path.rstrip("\\").endswith("link") or path.rstrip("\\").endswith("evil"):
            return 0x400
        return 0x10

    def read(path):
        leaf = path.rstrip("\\")
        if leaf.endswith("link"):
            return _reparse_buffer(_REPARSE_SYMLINK, "..\\evil")
        if leaf.endswith("evil"):
            return _reparse_buffer(_REPARSE_MOUNT_POINT, "\\??\\UNC\\server\\share")
        return None

    with patch("js_api._get_file_attributes", side_effect=attrs), \
         patch("js_api._read_reparse", side_effect=read), \
         patch("js_api.os.path.isfile") as fake_isfile:
        assert _path_crosses_reparse(local) is True
        assert _bg_path_acceptable(local) is False
    fake_isfile.assert_not_called()
    assert "C:\\Users\\evil" in seen
    assert all("a.png" not in path for path in seen)


def test_cloud_placeholder_background_is_local():
    local = "C:\\Users\\me\\OneDrive\\a.png"

    def attrs(path):
        if path.rstrip("\\").endswith("a.png"):
            return 0x400
        return 0x10

    def read(path):
        if path.rstrip("\\").endswith("a.png"):
            return (0x9000001A).to_bytes(4, "little") + b"\x00" * 8
        return None

    with patch("js_api._get_file_attributes", side_effect=attrs), \
         patch("js_api._read_reparse", side_effect=read):
        assert _path_crosses_reparse(local) is False


def test_unknown_reparse_tag_fails_closed():
    local = "C:\\Users\\me\\a.png"

    def attrs(path):
        if path.rstrip("\\").endswith("a.png"):
            return 0x400
        return 0x10

    def read(path):
        return (0xA0000004).to_bytes(4, "little") + b"\x00" * 24

    with patch("js_api._get_file_attributes", side_effect=attrs), \
         patch("js_api._read_reparse", side_effect=read), \
         patch("js_api.os.path.isfile") as fake_isfile:
        assert _path_crosses_reparse(local) is True
        assert _bg_path_acceptable(local) is False
    fake_isfile.assert_not_called()


def test_get_background_rejects_reparse_without_reading():
    api = _api()
    local = "C:\\Users\\me\\link\\a.png"
    with patch("js_api.load_config", return_value={"bg_path": local}), \
         patch("js_api._path_crosses_reparse", return_value=True), \
         patch("js_api.os.path.isfile") as fake_isfile, \
         patch("js_api.os.stat") as fake_stat:
        assert api.get_background() is None
    fake_isfile.assert_not_called()
    fake_stat.assert_not_called()


def test_get_background_rejects_unc_without_reading():
    api = _api()
    with patch("js_api.load_config",
               return_value={"bg_path": "\\\\host\\share\\a.png"}), \
         patch("js_api.os.path.isfile") as fake_isfile:
        assert api.get_background() is None
    fake_isfile.assert_not_called()