"""Smoke tests: import the split modules and exercise host/port/config guards."""
import json

import app as appmod
import config_store
import proxy_manager as pm


def test_version_constant_exists():
    assert isinstance(appmod.APP_VERSION, str) and appmod.APP_VERSION
    assert appmod.APP_VERSION == config_store.APP_VERSION


def test_valid_host_accepts_plain_hosts():
    assert appmod._valid_host("127.0.0.1")
    assert appmod._valid_host("proxy.local")
    assert config_store.valid_host("my-proxy.example.com")


def test_valid_host_rejects_injection():
    assert not appmod._valid_host("http://evil")
    assert not appmod._valid_host("1.2.3.4:80")
    assert not appmod._valid_host("a b")
    assert not appmod._valid_host("")


def test_is_valid_port():
    assert pm.is_valid_port(7897)
    assert pm.is_valid_port("7897")
    assert not pm.is_valid_port(0)
    assert not pm.is_valid_port(65536)
    assert not pm.is_valid_port("78a")
    assert not pm.is_valid_port(None)
    assert not pm.is_valid_port(True)


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    cfg = config_store.load_config()
    assert cfg["host"] == pm.DEFAULT_HOST
    cfg["port"] = "7897"
    assert config_store.save_config(cfg) is True
    again = config_store.load_config()
    assert again["port"] == "7897"


def test_load_config_sanitizes_bad_values(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"host": "http://evil", "port": "not-a-port", "scheme": "ftp"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    cfg = config_store.load_config()
    assert cfg["host"] == pm.DEFAULT_HOST
    assert cfg["port"] == pm.DEFAULT_PORT
    assert cfg["scheme"] == "http"
