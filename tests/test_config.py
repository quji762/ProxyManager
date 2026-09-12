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


def test_explicit_empty_ports_list_roundtrips(tmp_path, monkeypatch):
    """Regression: `if seen:` used to revive an explicit empty ports list
    into DEFAULT_PORTS on every load — the user's cleared preset list must
    survive save → load unchanged."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    cfg = config_store.load_config()
    cfg["ports"] = []
    assert config_store.save_config(cfg) is True
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["ports"] == []
    assert config_store.load_config()["ports"] == []


def test_missing_ports_key_still_defaults(tmp_path, monkeypatch):
    """No ports key at all (legacy/fresh config) keeps the defaults."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"host": "127.0.0.1"}), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    assert config_store.load_config()["ports"] == list(config_store.DEFAULT_PORTS)


def test_applied_roundtrip_and_legacy_fallback(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    # Legacy file: no applied key → form fields are used.
    cfg_file.write_text(json.dumps({"host": "10.0.0.1", "port": "1080"}),
                        encoding="utf-8")
    cfg = config_store.load_config()
    assert cfg["applied"]["host"] == "10.0.0.1"
    assert cfg["applied"]["port"] == "1080"
    cfg["applied"] = {"host": "127.0.0.1", "port": "7897",
                      "scheme": "socks5", "user": "alice", "password": "s3cret"}
    cfg["host"], cfg["port"] = "10.0.0.1", "1080"
    assert config_store.save_config(cfg) is True
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["applied"]["port"] == "7897"
    assert raw["applied"]["scheme"] == "socks5"
    assert str(raw["applied"]["password"]).startswith("DPAPI:")
    assert "s3cret" not in raw["applied"]["password"]
    back = config_store.load_config()
    assert back["applied"]["password"] == "s3cret"
    assert back["applied"]["user"] == "alice"
    assert back["host"] == "10.0.0.1"


def test_save_config_failure_keeps_previous_file(tmp_path, monkeypatch):
    """Regression: open('w') truncated config.json before dumping, so a
    mid-write crash (disk full) destroyed the file including the restore
    stash. The tmp-file + os.replace write must leave the old content intact."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"port": "7897", "restore": {"npm": "http://10.0.0.1:1"}}),
        encoding="utf-8")

    def _boom(obj, fh, *args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(config_store, "CONFIG_FILE", str(cfg_file))
        m.setattr(config_store.json, "dump", _boom)
        assert config_store.save_config({"port": "7890"}) is False
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert raw["port"] == "7897"
    assert raw["restore"] == {"npm": "http://10.0.0.1:1"}


def test_save_config_leaves_no_temp_file(tmp_path, monkeypatch):
    """The atomic write must clean up its tmp file and round-trip content."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    cfg = config_store.load_config()
    cfg["port"] = "7890"
    assert config_store.save_config(cfg) is True
    assert config_store.load_config()["port"] == "7890"
    assert list(tmp_path.iterdir()) == [cfg_file]


def test_public_aliases_exist():
    assert pm.config_dir is pm._config_dir
    assert pm.is_valid_port is pm._is_valid_port
    assert pm.get_wsl_gateway_ip is pm._get_wsl_gateway_ip
    assert callable(pm.log_exception)
    assert callable(pm.log_crash)
