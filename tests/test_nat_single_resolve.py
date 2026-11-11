"""Regression: NAT rewrite must be resolved ONCE per operation so the
written URL, the wsl_written record, and the user-facing note cannot
disagree. Also covers git half-write rollback, multi-distro read
continue-on-error, and the multi-distro enable rollback that must restore
the user's own pre-enable value instead of only removing our URL."""

from unittest.mock import patch

import pytest

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


class TestWslMultiDistroRollback:
    """多发行版 enable：一个发行版写入失败时，已写入的发行版必须回到用户
    启用前的原值——回滚只卸掉本工具的 URL 会把用户自己的代理配置一并删掉
    （git 的 --replace-all / _ini_edit 都是就地覆盖）。"""

    def test_gitwsl_rollback_writes_back_user_value(self, monkeypatch):
        """Ubuntu 的 http.proxy 原值 http://corp:8080、https.proxy 未设置；
        Debian 写入返回码 1。回滚必须写回原值并 unset https.proxy，且不得
        再走 --fixed-value 的“卸掉本工具 URL”老路。"""
        ran = []

        def fake_run(cmd, **kw):
            ran.append(list(cmd))
            script = cmd[-1]
            distro = cmd[2] if len(cmd) > 2 and cmd[1] == "-d" else ""

            class R:
                returncode = 0
                stderr = ""
                stdout = ""

            # 快照读取与 enable 脚本里都含 “git config --global --get”，
            # 必须用 startswith 区分；键名在 split()[4]。
            if script.startswith("git config --global --get"):
                if script.split()[4] == "http.proxy":
                    R.stdout = "http://corp:8080\n"
                else:
                    R.returncode = 1   # 键未设置 → None
                return R()
            if script.startswith("old_http=$(git config --global --get http.proxy"):
                if distro == "Debian":
                    R.returncode = 1
                    R.stderr = "boom"
                return R()
            return R()

        with patch.object(pm, "_wsl_targets", return_value=["Ubuntu", "Debian"]), \
             patch.object(pm, "wsl_available", return_value=True), \
             patch.object(pm.subprocess, "run", side_effect=fake_run):
            ret = pm.set_git_proxy("127.0.0.1", "7897", True, use_wsl=True)

        assert ret is not True
        assert "Debian" in str(ret)
        ubuntu = [c[-1] for c in ran
                  if len(c) > 2 and c[1] == "-d" and c[2] == "Ubuntu"]
        # 覆盖前先快照：两次 --get 之后才是写入
        assert ubuntu[0] == "git config --global --get http.proxy"
        assert ubuntu[1] == "git config --global --get https.proxy"
        joined = "\n".join(ubuntu)
        assert "--replace-all http.proxy 'http://corp:8080'" in joined
        assert "--unset-all https.proxy" in joined
        assert "--fixed-value" not in joined

    def test_gitwsl_single_distro_makes_no_snapshot_call(self, monkeypatch):
        """单目标时回滚循环不可能执行（要么全成功、要么 done 为空），所以
        不多打快照读取，命令序列与从前逐字一致。"""
        ran = []

        def fake_run(cmd, **kw):
            ran.append(list(cmd))

            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            return R()

        with patch.object(pm, "_wsl_targets", return_value=[""]), \
             patch.object(pm, "wsl_available", return_value=True), \
             patch.object(pm, "_wsl_git_config_get") as spy, \
             patch.object(pm.subprocess, "run", side_effect=fake_run):
            ret = pm.set_git_proxy("127.0.0.1", "7897", True, use_wsl=True)

        assert ret is True
        assert spy.call_count == 0
        assert len(ran) == 1                       # 只有一次 enable 写入
        assert ran[0][1] == "-e" and "-d" not in ran[0]

    def test_gitwsl_snapshot_read_failure_aborts_enable(self, monkeypatch):
        """读不到用户原值（git 缺失 / wsl.exe 失败 / 超时）就绝不覆盖：
        enable 整体失败，UI 回到实际状态。"""
        ran = []

        def fake_run(cmd, **kw):
            ran.append(list(cmd))

            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            return R()

        with patch.object(pm, "_wsl_targets", return_value=["Ubuntu", "Debian"]), \
             patch.object(pm, "wsl_available", return_value=True), \
             patch.object(pm, "_wsl_git_config_get",
                          side_effect=OSError("git config --get 失败（exit 127）")), \
             patch.object(pm.subprocess, "run", side_effect=fake_run):
            ret = pm.set_git_proxy("127.0.0.1", "7897", True, use_wsl=True)

        assert ret is not True
        assert "exit 127" in str(ret)
        assert all("--replace-all" not in " ".join(c) for c in ran)

    def test_npmwsl_rollback_restores_original_npmrc(self, monkeypatch):
        """Ubuntu 的 .npmrc 有用户原值（带行尾注释），Debian 读取失败。
        回滚写回原值并保留注释（_ini_edit 重发注释固定成一个前导空格），
        本工具的 URL 已卸掉。"""
        state = {"Ubuntu": {pm._NPMrc_PATH_WSL:
                            b"proxy = http://corp:8080  # corp\n"}}
        pushes = []

        def fake_pull(distro, path):
            if distro == "Debian":
                raise OSError("WSL(Debian): 读取 ~/.npmrc 失败（exit 1）")
            return state.get(distro, {}).get(path)

        def fake_push(distro, path, data):
            pushes.append((distro, data.decode("utf-8")))
            state.setdefault(distro, {})[path] = data

        with patch.object(pm, "_wsl_targets", return_value=["Ubuntu", "Debian"]), \
             patch.object(pm, "_wsl_pull_file", side_effect=fake_pull), \
             patch.object(pm, "_wsl_push_file", side_effect=fake_push):
            ret = pm.set_npm_proxy_wsl("127.0.0.1", "7897", True)

        assert ret is not True
        assert "Debian" in str(ret)
        # 先写进去过，回滚才谈得上“卸掉我们的 URL”
        assert any("http://127.0.0.1:7897" in body for _, body in pushes)
        final = state["Ubuntu"][pm._NPMrc_PATH_WSL].decode("utf-8")
        assert final == "proxy = http://corp:8080 # corp\n"

    def test_pipwsl_rollback_restores_original_pip_conf(self, monkeypatch):
        """pip 的 [global] proxy 同样必须在回滚后回到用户原值。"""
        state = {"Ubuntu": {pm._PIP_CONF_PATH_WSL:
                            b"[global]\nproxy = http://corp:3128\n"}}
        pushes = []

        def fake_pull(distro, path):
            if distro == "Debian":
                raise OSError("WSL(Debian): 读取 ~/.config/pip/pip.conf 失败（exit 1）")
            return state.get(distro, {}).get(path)

        def fake_push(distro, path, data):
            pushes.append((distro, data.decode("utf-8")))
            state.setdefault(distro, {})[path] = data

        with patch.object(pm, "_wsl_targets", return_value=["Ubuntu", "Debian"]), \
             patch.object(pm, "_wsl_pull_file", side_effect=fake_pull), \
             patch.object(pm, "_wsl_push_file", side_effect=fake_push):
            ret = pm.set_pip_proxy_wsl("127.0.0.1", "7897", True)

        assert ret is not True
        assert "Debian" in str(ret)
        assert any("http://127.0.0.1:7897" in body for _, body in pushes)
        final = state["Ubuntu"][pm._PIP_CONF_PATH_WSL].decode("utf-8")
        assert final == "[global]\nproxy = http://corp:3128\n"


class TestWslSnapshotHelpers:
    """快照 helper 的契约：读失败必须与“键未设置”可区分。"""

    def test_git_get_returns_value_or_none(self):
        out = {
            "git config --global --get http.proxy": (0, "http://corp:8080\n"),
            "git config --global --get https.proxy": (1, ""),
        }

        def fake_run(cmd, **kw):
            rc, text = out[cmd[-1]]

            class R:
                returncode = rc
                stderr = ""
                stdout = text
            return R()

        with patch.object(pm.subprocess, "run", side_effect=fake_run):
            assert pm._wsl_git_config_get("Ubuntu", "http.proxy") == \
                "http://corp:8080"
            # 键未设置是 rc=1 → None，不是异常
            assert pm._wsl_git_config_get("Ubuntu", "https.proxy") is None

    def test_git_get_raises_on_unknown_returncode(self):
        """git 缺失（127）不是“未设置”：当成 None 就会没有快照就覆盖。"""

        def fake_run(cmd, **kw):
            class R:
                returncode = 127
                stderr = ""
                stdout = ""
            return R()

        with (
            patch.object(pm.subprocess, "run", side_effect=fake_run),
            pytest.raises(OSError),
        ):
            pm._wsl_git_config_get("Ubuntu", "http.proxy")

    def test_git_set_replace_all_or_unset_all(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append((list(cmd), kw))

            class R:
                returncode = 0
                stderr = ""
            return R()

        with patch.object(pm.subprocess, "run", side_effect=fake_run):
            assert pm._wsl_git_config_set("Ubuntu", "http.proxy",
                                          "http://corp:8080") is True
            assert pm._wsl_git_config_set("Ubuntu", "https.proxy", None) is True

        (cmd, kw), (cmd2, _kw) = calls
        assert cmd[:6] == ["wsl", "-d", "Ubuntu", "-e", "bash", "-c"]
        assert cmd[-1] == \
            "git config --global --replace-all http.proxy 'http://corp:8080'"
        assert kw["timeout"] == 15
        assert cmd2[-1] == "git config --global --unset-all https.proxy"

    def test_git_set_raises_when_replace_all_fails(self):
        def fake_run(cmd, **kw):
            class R:
                returncode = 1
                stderr = "boom"
            return R()

        with (
            patch.object(pm.subprocess, "run", side_effect=fake_run),
            pytest.raises(OSError),
        ):
            pm._wsl_git_config_set("Ubuntu", "http.proxy",
                                   "http://corp:8080")

    def test_git_set_treats_absent_key_as_done(self):
        """--unset-all 对不存在的键退出 5，而那正是目标状态。"""

        def fake_run(cmd, **kw):
            class R:
                returncode = 5
                stderr = ""
            return R()

        with patch.object(pm.subprocess, "run", side_effect=fake_run):
            assert pm._wsl_git_config_set("Ubuntu", "https.proxy", None) is True

    def test_ini_snapshot_keeps_only_real_values(self):
        files = b"proxy = http://corp:8080  # corp\nregistry = https://r/\n"

        with patch.object(pm, "_wsl_pull_file", return_value=files):
            snap = pm._wsl_ini_snapshot("Ubuntu", "~/.npmrc",
                                        pm.NPM_PROXY_KEYS)
        # 用户没设 https-proxy：不能凭空造一个值进去
        assert snap == {"proxy": "http://corp:8080"}

        with patch.object(pm, "_wsl_pull_file", return_value=None):
            assert pm._wsl_ini_snapshot("Ubuntu", "~/.npmrc",
                                        pm.NPM_PROXY_KEYS) == {}
