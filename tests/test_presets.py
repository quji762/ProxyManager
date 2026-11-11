# -*- coding: utf-8 -*-
"""Named address presets: CRUD, DPAPI protection, sanitization, and apply.

A preset is a saved FORM address; applying it reuses switch_port's full-address
migration, so the applied/restore ownership semantics are identical to a port
switch. These tests pin that contract plus the storage safeguards.
"""

import json
from unittest.mock import patch

import config_store
from js_api import Api


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    return api


def _disk(tmp_path):
    return json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))


def test_save_and_list_preset_roundtrip(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    r = api.save_preset("work", "127.0.0.1", "7890", "socks5", "alice", "s3cret")
    assert r["ok"] is True
    listed = api.list_presets()
    assert len(listed) == 1
    p = listed[0]
    assert p["name"] == "work" and p["port"] == "7890"
    assert p["scheme"] == "socks5" and p["user"] == "alice"
    assert p["has_password"] is True
    # The list never carries the plaintext password.
    assert "password" not in p
    # On disk the password is DPAPI-encrypted, never plaintext.
    raw = _disk(tmp_path)
    assert str(raw["presets"][0]["password"]).startswith("DPAPI:")
    assert "s3cret" not in json.dumps(raw)


def test_get_preset_returns_decrypted_password(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.save_preset("work", "127.0.0.1", "7890", "http", "alice", "s3cret")
    p = api.get_preset("work")
    assert p["ok"] is True
    assert p["password"] == "s3cret"
    assert p["host"] == "127.0.0.1" and p["port"] == "7890"


def test_get_preset_missing(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    r = api.get_preset("nope")
    assert r["ok"] is False and r["error_key"] == "presetMissing"


def test_save_preset_upserts_by_name(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.save_preset("work", "127.0.0.1", "7890")
    api.save_preset("work", "127.0.0.1", "7892")
    listed = api.list_presets()
    assert len(listed) == 1
    assert listed[0]["port"] == "7892"


def test_save_preset_rejects_bad_name(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.save_preset("", "127.0.0.1", "7890")["error_key"] == "presetBadName"
    assert api.save_preset("x" * 41, "127.0.0.1", "7890")["error_key"] == "presetBadName"
    assert api.list_presets() == []


def test_save_preset_rejects_bad_address(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.save_preset("work", "bad host", "7890")["error_key"] == "errBadAddr"
    assert api.save_preset("work", "127.0.0.1", "99999")["error_key"] == "errBadAddr"
    assert api.list_presets() == []


def test_delete_preset(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.save_preset("work", "127.0.0.1", "7890")
    api.save_preset("home", "127.0.0.1", "7892")
    assert api.delete_preset("work")["ok"] is True
    names = [p["name"] for p in api.list_presets()]
    assert names == ["home"]


def test_load_presets_sanitizes(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    (tmp_path / "c.json").write_text(json.dumps({"presets": [
        {"name": "ok", "host": "127.0.0.1", "port": "7890",
         "scheme": "bogus", "user": "bad user!", "password": ""},
        {"name": "badhost", "host": "has space", "port": "7890"},
        {"name": "badport", "host": "127.0.0.1", "port": "abc"},
        {"name": "", "host": "127.0.0.1", "port": "7890"},
        "not-a-dict",
    ]}), encoding="utf-8")
    presets = config_store.load_config()["presets"]
    assert len(presets) == 1
    assert presets[0]["name"] == "ok"
    assert presets[0]["scheme"] == "http"      # sanitized
    assert presets[0]["user"] == ""            # sanitized


def test_load_presets_non_list_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    (tmp_path / "c.json").write_text(json.dumps({"presets": {"a": 1}}),
                                     encoding="utf-8")
    assert config_store.load_config()["presets"] == []


def test_apply_preset_migrates_enabled_endpoint(tmp_path, monkeypatch):
    """get_preset + switch_port: applying a preset re-applies only the enabled
    managed endpoint and moves applied_* — the same contract as a port switch."""
    api = _api(tmp_path, monkeypatch)
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    api.save_preset("work", "127.0.0.1", "7890", "http", "", "")
    p = api.get_preset("work")
    managed = "http://127.0.0.1:7897"
    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value="未设置"), \
         patch("js_api.pm.get_git_proxy_win", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value="未设置"), \
         patch.object(api, "refresh_item", return_value={"on": False, "git": "未设置"}), \
         patch("js_api.pm.set_ps_proxy", return_value=True) as set_ps, \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"):
        r = api.switch_port(p["port"], p["host"], p["scheme"], p["user"], p["password"])
    assert r["ok"] is True
    assert r["applied"] == ["ps"]
    assert api.applied_port == "7890"
    # The endpoint was disabled at the old address then enabled at the new one.
    assert ("127.0.0.1", "7897", False) in [c.args[:3] for c in set_ps.call_args_list]
    assert ("127.0.0.1", "7890", True) in [c.args[:3] for c in set_ps.call_args_list]