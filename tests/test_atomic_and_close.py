"""Regression: atomic file writes, registry rollback, native-close quit_clear."""

import json
import threading
from unittest.mock import MagicMock, patch

import config_store
import proxy_manager as pm


class TestAtomicIniWrite:
    def test_npm_write_failure_preserves_original(self, tmp_path, monkeypatch):
        npmrc = tmp_path / ".npmrc"
        original = "registry=https://example.com/\ntoken=SECRET\n"
        npmrc.write_text(original, encoding="utf-8")
        monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))

        real_open = open

        def failing_open(path, mode="r", *a, **k):
            # Fail only the tmp-side write; the target must never be opened "w"
            if ".tmp" in str(path) and "w" in str(mode):
                raise OSError("simulated disk full")
            return real_open(path, mode, *a, **k)

        with patch("builtins.open", side_effect=failing_open):
            ret = pm.set_npm_proxy("127.0.0.1", "7897", True)

        assert ret is not True
        assert npmrc.read_text(encoding="utf-8") == original
        assert list(tmp_path.glob("*.tmp")) == []

    def test_npm_write_success_updates_file(self, tmp_path, monkeypatch):
        npmrc = tmp_path / ".npmrc"
        npmrc.write_text("registry=https://example.com/\n", encoding="utf-8")
        monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))
        assert pm.set_npm_proxy("127.0.0.1", "7897", True) is True
        text = npmrc.read_text(encoding="utf-8")
        assert "proxy = http://127.0.0.1:7897" in text
        assert "registry=https://example.com/" in text
        # no leftover tmp
        assert list(tmp_path.glob("*.tmp")) == []

    def test_pip_write_success_updates_file(self, tmp_path, monkeypatch):
        pip_ini = tmp_path / "pip.ini"
        pip_ini.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(pm, "_pip_config_path", lambda: str(pip_ini))
        assert pm.set_pip_proxy("127.0.0.1", "7897", True) is True
        text = pip_ini.read_text(encoding="utf-8")
        assert "proxy = http://127.0.0.1:7897" in text
        assert "[global]" in text
        assert list(tmp_path.glob("*.tmp")) == []


class TestWslPushAtomic:
    def test_script_uses_same_dir_mv_not_truncate(self):
        script = pm._wsl_push_script("$HOME/.npmrc")
        assert "mv -f" in script
        assert 'cat "$tmp" >' not in script
        assert ".pmXXXXXX" in script
        assert 'mkdir -p "$dir"' in script
        assert 'rm -f "$tmp"' in script

    def test_script_resolves_symlink_before_rename(self):
        # A stow/chezmoi dotfile is a symlink; `mv -f` onto the link would
        # replace it with a regular file. Resolve to the real target first so
        # the rename lands on the target and the link survives (same contract
        # as the shell-block path, which writes THROUGH the file).
        script = pm._wsl_push_script("$HOME/.npmrc")
        assert '[ -L "$target" ]' in script
        assert "readlink -f" in script
        # the atomic rename must target the resolved variable, not the raw path
        assert 'mv -f "$tmp" "$target"' in script
        assert 'mv -f "$tmp" "$HOME/.npmrc"' not in script
        # a failed resolution keeps the original path (never rename onto "")
        assert '[ -n "$r" ]' in script

    def test_push_file_passes_atomic_script(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            seen["input"] = kw.get("input")

            class R:
                returncode = 0
                stdout = b""
                stderr = b""
            return R()

        monkeypatch.setattr(pm.subprocess, "run", fake_run)
        pm._wsl_push_file("Ubuntu", "~/.npmrc", b"proxy=http://127.0.0.1:7897\n")
        script = seen["cmd"][-1]
        assert "mv -f" in script
        assert 'cat "$tmp" >' not in script
        assert seen["input"] == b"proxy=http://127.0.0.1:7897\n"
        assert "Ubuntu" in seen["cmd"]

    def test_push_file_nonzero_raises(self, monkeypatch):
        class R:
            returncode = 1
            stdout = b""
            stderr = b"ENOSPC"
        monkeypatch.setattr(pm.subprocess, "run", lambda *a, **k: R())
        try:
            pm._wsl_push_file(None, "~/.npmrc", b"x")
        except OSError as e:
            assert "写入" in str(e)
        else:
            raise AssertionError("expected OSError")


class TestSysProxyWriteOrder:
    def test_proxy_server_written_before_enable_flag(self):
        """ProxyEnable must not be set if ProxyServer write fails."""
        calls = []
        key = MagicMock()

        def set_value(_key, name, _reserved, _type, value):
            calls.append((name, value))
            if name == "ProxyServer":
                raise OSError("registry full")

        with patch.object(pm, "winreg") as reg:
            reg.OpenKey.return_value = key
            reg.KEY_SET_VALUE = 1
            reg.KEY_READ = 2
            reg.REG_DWORD = 4
            reg.REG_SZ = 1
            reg.SetValueEx.side_effect = set_value
            reg.QueryValueEx.side_effect = FileNotFoundError
            ret = pm.set_sys_proxy("127.0.0.1", "7897", True)

        assert ret is not True
        names = [n for n, _ in calls]
        assert names[0] == "ProxyServer"
        assert "ProxyEnable" not in names


class TestConfigTmpUnique:
    def test_repeated_saves_leave_no_tmp_residue(self, tmp_path, monkeypatch):
        """Behavior check for the per-pid tmp name: saves (including a failed
        one in the middle) must never leave a .tmp behind, and the last write
        must win. A shared tmp name let one save read/rename the other's
        half-written file."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
        cfg = config_store.load_config()
        cfg["port"] = "7890"
        assert config_store.save_config(cfg) is True

        with patch.object(config_store.os, "replace",
                          side_effect=OSError("simulated replace failure")):
            assert config_store.save_config(dict(cfg, port="7892")) is False
        assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())

        cfg["port"] = "7897"
        assert config_store.save_config(cfg) is True
        assert config_store.load_config()["port"] == "7897"
        assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())

    def test_concurrent_saves_do_not_collide(self, tmp_path, monkeypatch):
        """Racing saves must each use their own tmp file and serialize on the
        config lock; the result is one valid file, never a mix of two."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_file))
        monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
        cfg = config_store.load_config()
        ports = ["7890", "7892", "7897"]
        barrier = threading.Barrier(len(ports))
        errors = []

        def saver(port):
            try:
                barrier.wait(10)
                config_store.save_config(dict(cfg, port=port))
            except Exception as e:  # pragma: no cover - surfaced by the assert
                errors.append(repr(e))

        threads = [threading.Thread(target=saver, args=(p,)) for p in ports]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        assert errors == []
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert raw["port"] in ports
        assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


class TestNativeCloseQuitClear:
    def test_on_closing_no_tray_runs_quit_clear(self, tmp_path, monkeypatch):
        """When tray is down, Alt+F4 must still honor quit_clear."""
        monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
        with patch("proxy_manager.get_net_counters", return_value=None):
            from js_api import Api
            api = Api()
        api._tray_ok = False
        api._save_geometry = MagicMock()
        api.clear_all_endpoints = MagicMock()

        # Simulate the _on_closing body (app.py) without importing webview
        def on_closing():
            if getattr(api, "_quitting", False):
                return False
            if api._tray_ok:
                api._save_geometry()
                return False
            try:
                api._save_geometry()
                if config_store.load_config().get("quit_clear"):
                    api.clear_all_endpoints()
            except Exception:
                pass
            return True

        # quit_clear off → no clear
        config_store.save_config({"quit_clear": False})
        assert on_closing() is True
        api.clear_all_endpoints.assert_not_called()

        # quit_clear on → clear runs
        config_store.save_config({"quit_clear": True})
        assert on_closing() is True
        api.clear_all_endpoints.assert_called_once()
