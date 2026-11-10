# -*- coding: utf-8 -*-
"""Autostart (HKCU ...\\Run) round-trip and save_config's failure paths.

Both had zero coverage. autostart_set / autostart_enabled / autostart_command
were never exercised at all, and save_config was only tested for the
json.dump-raises-OSError case. A fake winreg module is injected through
sys.modules (the functions `import winreg` lazily), so no real registry key
is ever read or written.
"""

import json
import os
import sys
import threading
import time
import types
from unittest import mock

import config_store


# ── fake winreg ──────────────────────────────────────────────────────────────
class _FakeKey:
    """Opaque handle stand-in; the fake registry keeps state in a dict."""


def _fake_winreg(monkeypatch, **failures):
    """Install an in-memory winreg in sys.modules; return (module, values)."""
    values = {}
    reg = types.ModuleType("winreg")
    reg.HKEY_CURRENT_USER = "HKCU"
    reg.KEY_READ = 0x20019
    reg.KEY_SET_VALUE = 0x20006
    reg.REG_SZ = 1

    def OpenKey(root, subkey, reserved=0, access=0):
        assert root == reg.HKEY_CURRENT_USER
        assert subkey == config_store.AUTOSTART_RUN
        if "open" in failures:
            raise failures["open"]
        return _FakeKey()

    def CloseKey(_key):
        pass

    def QueryValueEx(_key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], reg.REG_SZ

    def SetValueEx(_key, name, _reserved, _typ, value):
        if "set" in failures:
            raise failures["set"]
        values[name] = value

    def DeleteValue(_key, name):
        if "delete" in failures:
            raise failures["delete"]
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]

    reg.OpenKey = OpenKey
    reg.CloseKey = CloseKey
    reg.QueryValueEx = QueryValueEx
    reg.SetValueEx = SetValueEx
    reg.DeleteValue = DeleteValue
    monkeypatch.setitem(sys.modules, "winreg", reg)
    return reg, values


def _source_command():
    """What autostart_command() builds for a source (non-frozen) run."""
    return '"%s" "%s" --tray' % (sys.executable, config_store.ENTRY_SCRIPT)


class TestAutostartRoundTrip:
    def test_set_read_delete_roundtrip(self, monkeypatch):
        _reg, values = _fake_winreg(monkeypatch)
        assert values == {}
        assert config_store.autostart_enabled() is False

        assert config_store.autostart_set(True) is True
        assert values[config_store.AUTOSTART_NAME] == _source_command()
        assert config_store.autostart_enabled() is True

        assert config_store.autostart_set(False) is True
        assert config_store.AUTOSTART_NAME not in values
        assert config_store.autostart_enabled() is False

    def test_delete_is_idempotent(self, monkeypatch):
        """Clearing autostart twice must not fail (the value is already gone)
        — the tray menu can fire it repeatedly."""
        _reg, values = _fake_winreg(monkeypatch)
        assert config_store.autostart_set(True) is True
        assert config_store.autostart_set(False) is True
        assert config_store.autostart_set(False) is True
        assert values == {}

    def test_write_failure_returns_error_string(self, monkeypatch):
        _reg, values = _fake_winreg(monkeypatch, set=OSError(5, "access denied"))
        ret = config_store.autostart_set(True)
        assert ret is not True
        assert "denied" in str(ret)
        assert values == {}

    def test_enabled_false_when_run_key_missing(self, monkeypatch):
        _fake_winreg(monkeypatch, open=FileNotFoundError("no Run key"))
        assert config_store.autostart_enabled() is False

    def test_enabled_false_when_key_unreadable(self, monkeypatch):
        _fake_winreg(monkeypatch, open=OSError(5, "access denied"))
        assert config_store.autostart_enabled() is False


class TestAutostartCommandForms:
    def test_paths_with_spaces_and_non_ascii_are_quoted(self, monkeypatch):
        """A launcher path with spaces / a Chinese install path must survive
        the Run value: unquoted, cmd would split it and nothing would start."""
        _reg, values = _fake_winreg(monkeypatch)
        monkeypatch.setattr(sys, "executable",
                            r"C:\Program Files\Python 3.13\python.exe")
        monkeypatch.setattr(config_store, "ENTRY_SCRIPT",
                            r"D:\我的 的应用\ProxyManager\app.py")
        assert config_store.autostart_set(True) is True
        stored = values[config_store.AUTOSTART_NAME]
        assert stored == ('"%s" "%s" --tray'
                          % (sys.executable, config_store.ENTRY_SCRIPT))
        assert config_store.autostart_enabled() is True

    def test_frozen_form_is_executable_only(self, monkeypatch):
        """A --onefile build starts from sys.executable alone (ENTRY_SCRIPT
        points into the transient _MEIPASS dir and is gone after exit)."""
        _reg, values = _fake_winreg(monkeypatch)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", r"C:\Program Files\ProxyManager\ProxyManager.exe")
        assert config_store.autostart_command() == '"%s" --tray' % sys.executable
        assert config_store.autostart_set(True) is True
        assert values[config_store.AUTOSTART_NAME] == '"%s" --tray' % sys.executable
        assert config_store.autostart_enabled() is True

    def test_frozen_form_matches_stale_frozen_value(self, monkeypatch):
        """The frozen branch of autostart_enabled must accept a value left by
        an older build at the same exe path."""
        _reg, values = _fake_winreg(monkeypatch)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        values[config_store.AUTOSTART_NAME] = '"%s" --tray' % sys.executable
        assert config_store.autostart_enabled() is True

    def test_legacy_entry_script_value_still_counts_as_enabled(self, monkeypatch):
        """A Run value written by an older build (different interpreter, same
        app.py) is still ours — do not offer to "enable" what is already on."""
        _reg, values = _fake_winreg(monkeypatch)
        values[config_store.AUTOSTART_NAME] = \
            '"C:\\Old\\Python\\python.exe" "%s" --tray' % config_store.ENTRY_SCRIPT
        assert config_store.autostart_enabled() is True

    def test_unrelated_stale_value_is_not_ours(self, monkeypatch):
        _reg, values = _fake_winreg(monkeypatch)
        values[config_store.AUTOSTART_NAME] = \
            '"C:\\other\\python.exe" "C:\\other\\app.py" --tray'
        assert config_store.autostart_enabled() is False


# ── save_config failure paths ────────────────────────────────────────────────
def _seed(tmp_path, monkeypatch, content):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    return cfg_file


def _tmp_names(tmp_path):
    return [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


class TestSaveConfigWriteFailures:
    def test_unserializable_value_returns_false(self, tmp_path, monkeypatch):
        """json.dump raises TypeError for a value it cannot serialize; that
        used to escape to pywebview and leave the tmp file behind."""
        cfg_file = _seed(tmp_path, monkeypatch, {"port": "7897"})
        assert config_store.save_config({"restore": {"npm": {1, 2}}}) is False
        assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"port": "7897"}
        assert _tmp_names(tmp_path) == []

    def test_replace_failure_keeps_previous_file(self, tmp_path, monkeypatch):
        cfg_file = _seed(tmp_path, monkeypatch,
                         {"port": "7897", "restore": {"npm": "http://10.0.0.1:1"}})
        real_replace = os.replace
        target = os.path.abspath(str(cfg_file))

        def failing_replace(src, dst):
            if os.path.abspath(dst) == target:
                raise OSError("simulated replace failure")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", failing_replace)
        assert config_store.save_config({"port": "7890"}) is False
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert raw["port"] == "7897"
        assert raw["restore"] == {"npm": "http://10.0.0.1:1"}
        assert _tmp_names(tmp_path) == []

    def test_config_dir_creation_failure_returns_false(self, tmp_path, monkeypatch):
        """os.makedirs(APP_DIR) failing (read-only parent, disk full) must be
        reported as False, not raised."""
        cfg_file = _seed(tmp_path, monkeypatch, {"port": "7897"})
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(config_store, "APP_DIR",
                            str(blocker / "ProxyManager"))
        assert config_store.save_config({"port": "7890"}) is False
        assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"port": "7897"}
        assert _tmp_names(tmp_path) == []

    def test_bak_write_failure_does_not_fail_the_save(self, tmp_path, monkeypatch):
        """The .bak refresh is best-effort: the primary write already landed,
        so a failing bak must neither fail the save nor leave its tmp behind."""
        cfg_file = _seed(tmp_path, monkeypatch, {"port": "7897"})
        real_replace = os.replace
        target = os.path.abspath(str(cfg_file) + ".bak")

        def failing_replace(src, dst):
            if os.path.abspath(dst) == target:
                raise OSError("simulated bak failure")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", failing_replace)
        assert config_store.save_config({"port": "7890"}) is True
        assert json.loads(cfg_file.read_text(encoding="utf-8"))["port"] == "7890"
        assert _tmp_names(tmp_path) == []
        assert not (tmp_path / "config.json.bak").exists()


class TestSaveConfigMergeInput:
    def test_corrupt_primary_quarantined_not_merged_into_empty(
            self, tmp_path, monkeypatch):
        """An unreadable primary used to be swallowed as {} — the save then
        wrote back only its own keys, silently destroying every other key on
        disk. It must be moved aside as .corrupt instead."""
        cfg_file = _seed(tmp_path, monkeypatch, {})
        cfg_file.write_text("{truncated", encoding="utf-8")
        assert config_store.save_config({"port": "7890"}) is True
        assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"port": "7890"}
        quarantined = tmp_path / "config.json.corrupt"
        assert quarantined.read_text(encoding="utf-8") == "{truncated"

    def test_non_object_primary_quarantined(self, tmp_path, monkeypatch):
        cfg_file = _seed(tmp_path, monkeypatch, {})
        cfg_file.write_text("[]", encoding="utf-8")
        assert config_store.save_config({"port": "7890"}) is True
        assert json.loads(cfg_file.read_text(encoding="utf-8")) == {"port": "7890"}
        assert (tmp_path / "config.json.corrupt").read_text(encoding="utf-8") == "[]"


class TestCrossProcessConfigLock:
    def test_lock_file_is_created_next_to_the_config(self, tmp_path, monkeypatch):
        """The lock is what stops a second instance from clobbering the file:
        if it silently disappears, the race regression is back."""
        _seed(tmp_path, monkeypatch, {"port": "7897"})
        assert config_store.save_config({"port": "7890"}) is True
        assert (tmp_path / "config.json.lock").is_file()

    def test_second_holder_waits_for_the_first(self, tmp_path, monkeypatch):
        """Two holders of the lock must serialize on the byte-range lock
        itself — not just on the process-local _cfg_lock."""
        _seed(tmp_path, monkeypatch, {"port": "7897"})
        order = []
        first = config_store._cross_process_lock()
        first.__enter__()
        try:
            def second_holder():
                with config_store._cross_process_lock():
                    order.append("second")

            t = threading.Thread(target=second_holder, daemon=True)
            t.start()
            time.sleep(0.3)
            order.append("first-released")
        finally:
            first.__exit__(None, None, None)
        t.join(10)
        assert order == ["first-released", "second"]

    def test_lock_failure_degrades_and_still_saves(self, tmp_path, monkeypatch):
        """An unusable lock file must never make saving impossible — the save
        proceeds with the in-process lock alone."""
        cfg_file = _seed(tmp_path, monkeypatch, {"port": "7897"})
        with mock.patch.object(config_store.msvcrt, "locking",
                               side_effect=OSError("locking unavailable")):
            assert config_store.save_config({"port": "7890"}) is True
        assert json.loads(cfg_file.read_text(encoding="utf-8"))["port"] == "7890"
        assert _tmp_names(tmp_path) == []
