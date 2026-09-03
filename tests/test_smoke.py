"""Smoke tests: import both modules and exercise the pure helpers that guard
system-touching operations (host/port validation, config load/save round-trip).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import proxy_manager as pm  # noqa: E402


def test_version_constant_exists():
    assert isinstance(appmod.APP_VERSION, str) and appmod.APP_VERSION


def test_valid_host_accepts_plain_hosts():
    assert appmod._valid_host("127.0.0.1")
    assert appmod._valid_host("proxy.local")


def test_valid_host_rejects_injection():
    assert not appmod._valid_host("http://evil")      # scheme
    assert not appmod._valid_host("1.2.3.4:80")       # port smuggled in host
    assert not appmod._valid_host("a b")              # whitespace
    assert not appmod._valid_host("")                 # empty


def test_is_valid_port():
    assert pm._is_valid_port(7897)
    assert pm._is_valid_port("7897")
    assert not pm._is_valid_port(0)
    assert not pm._is_valid_port(65536)
    assert not pm._is_valid_port("78a")
    assert not pm._is_valid_port(None)


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "CONFIG_FILE", str(tmp_path / "config.json"))
    cfg = appmod.load_config()
    assert cfg["host"] == pm.DEFAULT_HOST
    cfg["port"] = "7897"
    assert appmod.save_config(cfg) is True
    again = appmod.load_config()
    assert again["port"] == "7897"


def test_load_config_sanitizes_bad_values(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"host": "http://evil", "port": "not-a-port"}), encoding="utf-8"
    )
    monkeypatch.setattr(appmod, "CONFIG_FILE", str(cfg_file))
    cfg = appmod.load_config()
    assert cfg["host"] == pm.DEFAULT_HOST
    assert cfg["port"] == pm.DEFAULT_PORT
