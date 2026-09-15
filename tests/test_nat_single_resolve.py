# -*- coding: utf-8 -*-
"""Regression: NAT rewrite must be resolved ONCE per operation so the
written URL, the wsl_written record, and the user-facing note cannot
disagree. Also covers git half-write rollback and multi-distro read
continue-on-error."""

from unittest.mock import patch

import config_store
import proxy_manager as pm
from js_api import Api


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


class TestNatSingleResolve:
    """npmwsl enable: one gateway probe → write and record share the host."""

    def test_npmwsl_write_and_record_stay_in_sync_when_gateway_flaps(
            self, tmp_path, monkeypatch):
        """Previously the setter probed the gateway itself and
        _record_wsl_write probed again — flapping results desynced them."""
        api = _make_api(tmp_path, monkeypatch)
        written = {}

        def setter(host, port, enable=True, scheme="http", user="", password=""):
            # Mimic the NEW setter: host is used as-is (no internal nat_host).
            if enable:
                written["url"] = pm.build_proxy_url(host, port, scheme)
            return True

        # Gateway available on every call (js_api now only needs one).
        with patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
             patch("js_api.pm.get_wsl_gateway_ip", return_value="172.24.0.1"), \
             patch("js_api.pm.set_npm_proxy_wsl", side_effect=setter), \
             patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET):
            r = api.do_toggle("npmwsl", True, "127.0.0.1", "7897")

        assert r["ok"] is True
        assert written["url"] == "http://172.24.0.1:7897"
        assert api._wsl_written["npmwsl"] == written["url"]
        assert api._is_managed_wsl_url(written["url"], "npmwsl") is True
        assert "已自动改用宿主网关 IP" in (r.get("note") or "")

    def test_npmwsl_gateway_unavailable_warns_and_records_loopback(
            self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        written = {}

        def setter(host, port, enable=True, scheme="http", user="", password=""):
            if enable:
                written["url"] = pm.build_proxy_url(host, port, scheme)
            return True

        with patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
             patch("js_api.pm.get_wsl_gateway_ip", return_value=None), \
             patch("js_api.pm.set_npm_proxy_wsl", side_effect=setter), \
             patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET):
            r = api.do_toggle("npmwsl", True, "127.0.0.1", "7897")

        assert r["ok"] is True
        # 未拿到网关：写入与记录都是回环，且不得谎称“已改用网关”
        assert written["url"] == "http://127.0.0.1:7897"
        assert api._wsl_written["npmwsl"] == written["url"]
        note = r.get("note") or ""
        assert "已自动改用宿主网关 IP" not in note
        assert "无法获取宿主网关" in note

    def test_wsl_shell_block_note_matches_actual_rewrite(
            self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        calls = []

        def setter(host, port, enable=True, **kw):
            calls.append((host, str(port), enable))
            return True

        with patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
             patch("js_api.pm.get_wsl_gateway_ip", return_value="172.25.0.1"), \
             patch("js_api.pm.set_wsl_proxy", side_effect=setter):
            r = api.do_toggle("wsl", True, "127.0.0.1", "7897")
        assert r["ok"] is True
        # 写入的是解析后的网关，不是原始 127.0.0.1
        assert calls == [("172.25.0.1", "7897", True)]
        assert "已自动改用宿主网关 IP" in (r.get("note") or "")

    def test_mirrored_mode_keeps_loopback_without_nat_note(
            self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        calls = []

        def setter(host, port, enable=True, **kw):
            calls.append((host, str(port), enable))
            return True

        with patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"), \
             patch("js_api.pm.set_wsl_proxy", side_effect=setter):
            r = api.do_toggle("wsl", True, "127.0.0.1", "7897")
        assert r["ok"] is True
        assert calls == [("127.0.0.1", "7897", True)]
        note = r.get("note") or ""
        assert "网关" not in note


class TestGitHalfWriteRollback:
    def test_windows_enable_rolls_back_http_when_https_fails(self, monkeypatch):
        ran = []

        def fake_run(cmd, **kw):
            ran.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            if len(cmd) > 4 and cmd[3] == "--replace-all" and cmd[4] == "https.proxy":
                R.returncode = 1
                R.stderr = "boom"
            return R()

        with patch.object(pm.subprocess, "run", side_effect=fake_run):
            ret = pm.set_git_proxy("127.0.0.1", "7897", True, use_wsl=False)

        assert ret is not True
        keys = [c[4] for c in ran if len(c) > 4]
        assert "http.proxy" in keys and "https.proxy" in keys
        # 写入前没有用户值：回滚必须 unset，不能把用户代理留在半开状态
        last = ran[-1]
        assert last[3] == "--unset-all" and last[4] == "http.proxy"

    def test_windows_enable_restores_previous_http_proxy(self, monkeypatch):
        """https.proxy 失败时必须把 http.proxy 写回用户原值，而不是 unset。"""
        ran = []

        def fake_run(cmd, **kw):
            ran.append(list(cmd))
            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            if cmd[3:] == ["--get", "http.proxy"]:
                R.stdout = "http://corp:8080\n"
            if len(cmd) > 4 and cmd[3] == "--replace-all" and cmd[4] == "https.proxy":
                R.returncode = 1
                R.stderr = "boom"
            return R()

        with patch.object(pm.subprocess, "run", side_effect=fake_run):
            ret = pm.set_git_proxy("127.0.0.1", "7897", True, use_wsl=False)

        assert ret is not True
        last = ran[-1]
        assert last[3] == "--replace-all" and last[4] == "http.proxy"
        assert last[5] == "http://corp:8080"

    def test_do_toggle_git_disable_keeps_managed_url_on_failure(
            self, tmp_path, monkeypatch):
        api = _make_api(tmp_path, monkeypatch)
        api._git_managed["gitwin"] = "http://127.0.0.1:7897"
        with patch("js_api.pm.get_git_proxy_win",
                   return_value="http://127.0.0.1:7897"), \
             patch("js_api.pm.set_git_proxy", return_value="disk full"):
            r = api.do_toggle("gitwin", False, "127.0.0.1", "7897")
        assert r.get("error")
        # 失败时不得清空，否则下次 disable 不知道该卸哪条 URL
        assert api._git_managed["gitwin"] == "http://127.0.0.1:7897"


class TestMultiDistroRead:
    def test_npm_wsl_continues_after_first_distro_fails(self, monkeypatch):
        attempts = []

        def fake_pull(distro, path):
            attempts.append(distro)
            if distro == "Ubuntu":
                raise OSError("broken")
            return b"proxy = http://172.24.0.1:7897\n"

        with patch.object(pm, "_wsl_targets", return_value=["Ubuntu", "Debian"]), \
             patch.object(pm, "_wsl_pull_file", side_effect=fake_pull):
            v = pm.get_npm_proxy_wsl()

        assert v == "http://172.24.0.1:7897"
        assert attempts == ["Ubuntu", "Debian"]

    def test_npm_wsl_all_fail_reports_undeclared(self, monkeypatch):
        def fake_pull(distro, path):
            raise OSError("broken")

        with patch.object(pm, "_wsl_targets", return_value=["A", "B"]), \
             patch.object(pm, "_wsl_pull_file", side_effect=fake_pull):
            assert pm.get_npm_proxy_wsl() == pm.UNDETECTED

    def test_pip_wsl_continues_after_first_distro_fails(self, monkeypatch):
        attempts = []

        def fake_pull(distro, path):
            attempts.append((distro, path))
            if distro == "Ubuntu":
                raise OSError("broken")
            if path.endswith("pip.conf") and "config" in path:
                return b"[global]\nproxy = http://10.0.0.1:3128\n"
            return None

        with patch.object(pm, "_wsl_targets", return_value=["Ubuntu", "Debian"]), \
             patch.object(pm, "_wsl_pull_file", side_effect=fake_pull):
            v = pm.get_pip_proxy_wsl()

        assert v == "http://10.0.0.1:3128"
        assert any(d == "Debian" for d, _ in attempts)
