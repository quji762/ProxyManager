"""save_config field-level regressions: null host/user/password must never
become the string "None" on disk, and an explicit empty ports list must not
be silently revived into DEFAULT_PORTS. No real registry/endpoint is touched
(Api.save_config only mutates the in-memory cache and config.json)."""

import json
from unittest.mock import patch

import config_store
from js_api import Api


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_null_host_user_password_keep_previous_values(tmp_path, monkeypatch):
    """null 一律跳过该字段更新：str(None)="None" 曾被当合法值落库（host 是
    纯字母，valid_host 挡不住；user/password 过 is_valid_userinfo(None)=True），
    password 甚至会被 DPAPI 加密成 "None" 的密文。"""
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.user, api.password = "10.0.0.5", "alice", "s3cret"
    assert api.save_config(
        {"host": None, "user": None, "password": None}) is True
    assert api.host == "10.0.0.5"
    assert api.user == "alice"
    assert api.password == "s3cret"

    cfg = config_store.load_config()
    assert cfg["host"] == "10.0.0.5"
    assert cfg["user"] == "alice"
    assert cfg["password"] == "s3cret"


def test_empty_host_string_keeps_previous_value(tmp_path, monkeypatch):
    """host 为空串同样跳过（valid_host('') 恒 False），不覆盖旧值。"""
    api = _make_api(tmp_path, monkeypatch)
    api.host = "10.0.0.5"
    assert api.save_config({"host": ""}) is True
    assert api.host == "10.0.0.5"
    assert config_store.load_config()["host"] == "10.0.0.5"


def test_empty_user_password_still_clears(tmp_path, monkeypatch):
    """空串＝清空凭据，仍然采纳：前端没有独立的"清除凭据"按钮，但 authRow
    的两个输入框可以直接删空，change → applyAddress → switch_port /
    persistSoon 会把空串传下来——这是 UI 实际存在的清空凭据交互。"""
    api = _make_api(tmp_path, monkeypatch)
    api.user, api.password = "alice", "s3cret"
    assert api.save_config({"user": "", "password": ""}) is True
    assert api.user == ""
    assert api.password == ""
    cfg = config_store.load_config()
    assert cfg["user"] == ""
    assert cfg["password"] == ""


def test_explicit_empty_ports_list_not_revived(tmp_path, monkeypatch):
    """用户删光预设保存 ports: [] 后，重新加载必须还是 []（原先被
    `or DEFAULT_PORTS` 静默复活成默认端口）。"""
    api = _make_api(tmp_path, monkeypatch)
    assert api.save_config({"ports": []}) is True
    assert config_store.load_config()["ports"] == []

    # 之后保存别的字段也不会把端口悄悄填回来
    assert api.save_config({"lang": "en"}) is True
    cfg = config_store.load_config()
    assert cfg["ports"] == []
    assert cfg["lang"] == "en"

    raw = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert raw["ports"] == []


def test_garbage_ports_entries_sanitized_to_empty(tmp_path, monkeypatch):
    """键存在但全是非法项 → 清洗为空列表（不再回落默认）；合法项保留。"""
    api = _make_api(tmp_path, monkeypatch)
    assert api.save_config({"ports": ["abc", None, 70000]}) is True
    assert config_store.load_config()["ports"] == []
    assert api.save_config({"ports": ["7897", 0, 65536, "7890", "7897"]}) is True
    assert config_store.load_config()["ports"] == [7897, 7890]


def test_missing_ports_key_still_defaults(tmp_path, monkeypatch):
    """配置里无 ports 键时仍用默认（显式空列表才原样保留）。"""
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({"host": "127.0.0.1"}), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    assert config_store.load_config()["ports"] == list(config_store.DEFAULT_PORTS)
