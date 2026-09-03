"""Config load: legacy git key, scheme/user sanitization, speed_full migrate."""

import json

import config_store
import proxy_manager as pm


def test_legacy_git_key_splits(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"git": True}), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    cfg = config_store.load_config()
    assert cfg["git_win"] is True
    assert cfg["git_wsl"] is True


def test_bad_userinfo_dropped(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"user": "a:b", "password": "x y", "lang": "de", "scheme": "socks5"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    cfg = config_store.load_config()
    assert cfg["user"] == ""
    assert cfg["password"] == ""
    assert cfg["lang"] == "zh"
    assert cfg["scheme"] == "socks5"


def test_speed_full_default_migrates_from_5(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"speed_full": 5}), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    cfg = config_store.load_config()
    assert cfg["speed_full"] == 40


def test_speed_full_migration_persisted(tmp_path, monkeypatch):
    """Regression: the speed_full=5→40 migration should be written back so
    subsequent loads don't re-apply it every time."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"speed_full": 5}), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    config_store.load_config()
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw.get("speed_full") == 40


def test_save_preserves_unknown_keys(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"future_flag": True, "port": "1080"}), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    cfg = config_store.load_config()
    cfg["port"] = "7890"
    assert config_store.save_config(cfg) is True
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["future_flag"] is True
    assert raw["port"] == "7890"


def test_public_aliases_exist():
    assert pm.config_dir is pm._config_dir
    assert pm.is_valid_port is pm._is_valid_port
    assert pm.get_wsl_gateway_ip is pm._get_wsl_gateway_ip
    assert callable(pm.log_exception)
    assert callable(pm.log_crash)
