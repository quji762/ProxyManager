"""WSL-side "last written URL" record (wsl_written): _is_managed_wsl_url must
only treat a file URL as this tool's when it EXACTLY equals the persisted URL
the tool last wrote to that endpoint. A user-configured LAN proxy that merely
shares the applied port is user state: stashed on enable, restored on disable,
never deleted by switch_port. Everything runs against fakes — no real WSL,
registry or endpoint is touched."""

import json
from contextlib import ExitStack
from unittest.mock import patch

import config_store
import proxy_manager as pm
from js_api import Api


class FakeWslEndpoint:
    """In-memory stand-in for one WSL-side endpoint (npmrc / pip.conf / git
    config). Applies the same NAT loopback→gateway rewrite as the real pm
    setters, so the stored URL matches what _record_wsl_write computes."""

    def __init__(self, gateway):
        self.url = None
        self.gateway = gateway
        self.calls = []

    def get(self):
        return self.url or pm.UNSET

    def setter(self, host, port, enable=True, scheme="http",
               user="", password=""):
        self.calls.append((host, str(port), enable))
        if enable:
            write_host = self.gateway \
                if str(host).lower() in pm.LOOPBACK_HOSTS else host
            self.url = pm.build_proxy_url(write_host, port, scheme, user,
                                          password)
        elif self.url == pm.build_proxy_url(host, port, scheme, user,
                                            password):
            self.url = None  # surgical unset, same contract as pm
        return True


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def _npmwsl_patches(fake):
    """Patch the npm(WSL) endpoint + NAT mode/gateway against `fake`. The
    gateway value is captured when the context is ENTERED, so a test can
    move fake.gateway between two with-blocks to simulate a subnet change."""
    stack = ExitStack()
    stack.enter_context(
        patch("js_api.pm.get_wsl_network_mode", return_value="NAT"))
    stack.enter_context(
        patch("js_api.pm.get_wsl_gateway_ip", return_value=fake.gateway))
    stack.enter_context(
        patch("js_api.pm.get_npm_proxy_wsl", side_effect=fake.get))
    stack.enter_context(
        patch("js_api.pm.set_npm_proxy_wsl", side_effect=fake.setter))
    return stack


def test_is_managed_wsl_url_requires_exact_record_match(tmp_path, monkeypatch):
    """The judgment is exact equality with the per-endpoint record only —
    the old port/scheme/userinfo heuristic is gone."""
    api = _make_api(tmp_path, monkeypatch)
    api._wsl_written["npmwsl"] = "http://172.24.0.1:7897"
    assert api._is_managed_wsl_url("http://172.24.0.1:7897", "npmwsl") is True
    assert api._is_managed_wsl_url("  http://172.24.0.1:7897  ",
                                   "npmwsl") is True
    # 记录按端点隔离：写进 npm 的 URL 不是 git 的
    assert api._is_managed_wsl_url("http://172.24.0.1:7897", "gitwsl") is False
    # 同端口不同 host（用户 LAN 代理）不再是"本工具写的"
    assert api._is_managed_wsl_url("http://192.168.1.50:7897", "npmwsl") is False
    assert api._is_managed_wsl_url("http://172.24.0.1:7898", "npmwsl") is False
    assert api._is_managed_wsl_url(pm.UNSET, "npmwsl") is False
    # 无记录（从未写过/记录丢失）一律当作用户状态
    api._wsl_written["npmwsl"] = None
    assert api._is_managed_wsl_url("http://172.24.0.1:7897", "npmwsl") is False
    # 非 WSL 侧端点不归它管
    assert api._is_managed_wsl_url("http://172.24.0.1:7897", "npm") is False


def test_user_lan_proxy_stashed_and_restored(tmp_path, monkeypatch):
    """用户手工配置的同端口 LAN 代理：enable 前被暂存为用户状态，disable
    后原样恢复（修复前被当成本工具的陈旧网关直接删掉重写）。"""
    api = _make_api(tmp_path, monkeypatch)
    user_url = "http://192.168.1.50:7897"
    fake = FakeWslEndpoint("172.24.0.1")
    fake.url = user_url
    with _npmwsl_patches(fake):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("npmwsl") == user_url   # 暂存为用户状态
        assert fake.url == "http://172.24.0.1:7897"     # 覆盖为工具地址
        r = api.do_toggle("npmwsl", False)

    assert r["ok"] is True
    assert r.get("restored") is True
    assert fake.url == user_url                         # 原样恢复
    assert api._restore.get("npmwsl") is None
    # 恢复路径写入的是用户 URL，不得记成"本工具写的"
    assert api._wsl_written["npmwsl"] == "http://172.24.0.1:7897"


def test_tool_written_gateway_url_not_stashed(tmp_path, monkeypatch):
    """工具自己写入的网关形态 URL（记录在案）：再次启用不得当用户状态暂存，
    否则关闭时会把它"恢复"回来，需要点两次。"""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakeWslEndpoint("172.24.0.1")
    with _npmwsl_patches(fake):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
        assert api._wsl_written["npmwsl"] == "http://172.24.0.1:7897"
        assert api._restore.get("npmwsl") is None
        # 文件仍是网关形态，再次启用（改端口前后的常见操作）
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("npmwsl") is None


def test_stale_gateway_url_matching_record_not_stashed(tmp_path, monkeypatch):
    """NAT 子网在工具外变化：文件里的 URL 恰好等于"上次写入 URL"记录（工具
    写入后文件未变，变的只是当前网关）→ 是本工具的陈旧网关，不暂存，并用
    新网关改写、更新记录。这一步只有记录层能命中（_ours_proxy_urls 的当前
    网关形态已经不匹配）。"""
    api = _make_api(tmp_path, monkeypatch)
    fake = FakeWslEndpoint("172.24.0.1")
    with _npmwsl_patches(fake):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
    # 子网变化：网关 172.24.0.1 → 172.25.0.1，文件保持旧值
    fake.gateway = "172.25.0.1"
    with _npmwsl_patches(fake):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
        assert api._restore.get("npmwsl") is None
        assert fake.url == "http://172.25.0.1:7897"
        assert api._wsl_written["npmwsl"] == "http://172.25.0.1:7897"


def test_switch_port_does_not_touch_user_url(tmp_path, monkeypatch):
    """端口切换只迁移"等于记录"的端点：用户的同端口 LAN 代理必须原样保留。"""
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    user_url = "http://192.168.1.50:7897"
    fake = FakeWslEndpoint("172.24.0.1")
    fake.url = user_url
    ps_calls = []

    with patch("js_api.pm.get_ps_status",
               return_value="http://127.0.0.1:7897"), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", side_effect=fake.get), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_ps_proxy",
               side_effect=lambda *a, **k: ps_calls.append(a) or True), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=fake.setter):
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is True
    assert r["applied"] == ["ps"]       # 只有 ps 迁移
    assert ps_calls == [("127.0.0.1", "7897", False), ("127.0.0.1", "7890", True)]
    assert fake.calls == []             # npmwsl 从未被读写
    assert fake.url == user_url         # 用户配置原样保留


def test_record_persists_across_restart_and_switch_migrates(tmp_path,
                                                            monkeypatch):
    """记录随 config.json 持久化：重启后 switch_port 仍能认出"这是本工具
    写的"并迁移到新端口。"""
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    api = _make_api(tmp_path, monkeypatch)
    fake = FakeWslEndpoint("172.24.0.1")
    with _npmwsl_patches(fake):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True

    # 模拟重启：新实例从磁盘读回记录
    api2 = _make_api(tmp_path, monkeypatch)
    assert api2._wsl_written["npmwsl"] == "http://172.24.0.1:7897"

    fake2 = FakeWslEndpoint("172.24.0.1")
    fake2.url = "http://172.24.0.1:7897"
    with _npmwsl_patches(fake2), \
         patch.object(api2, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}):
        r = api2.switch_port("7890", "127.0.0.1")

    assert r["ok"] is True
    assert r["applied"] == ["npmwsl"]
    assert fake2.calls == [("172.24.0.1", "7897", False),
                           ("127.0.0.1", "7890", True)]
    assert fake2.url == "http://172.24.0.1:7890"
    assert api2._wsl_written["npmwsl"] == "http://172.24.0.1:7890"


def test_gitwsl_record_set_on_enable_cleared_on_disable(tmp_path, monkeypatch):
    """gitwsl 启用记录实际写入的网关形态 URL；关闭成功后清掉——之后用户
    手写的任何 URL（哪怕碰巧相同）都按用户状态处理。"""
    api = _make_api(tmp_path, monkeypatch)
    git_calls = []
    with patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
         patch("js_api.pm.get_wsl_gateway_ip", return_value="172.24.0.1"), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_git_proxy",
               side_effect=lambda *a, **k: git_calls.append(a) or True):
        assert api.do_toggle("gitwsl", True, "127.0.0.1", "7897")["ok"] is True
        assert api._wsl_written["gitwsl"] == "http://172.24.0.1:7897"
        assert api._git_managed["gitwsl"] == "http://172.24.0.1:7897"
        assert api.do_toggle("gitwsl", False)["ok"] is True

    assert api._wsl_written["gitwsl"] is None
    assert api._git_managed["gitwsl"] is None
    assert git_calls == [("172.24.0.1", "7897", True),
                         ("172.24.0.1", "7897", False)]


def test_record_encrypted_at_rest(tmp_path, monkeypatch):
    """记录可能携带 applied 凭据：落盘必须 DPAPI 加密（与 restore 暂存同
    机制）。真实 DPAPI 保证"磁盘是密文"的断言有区分度。"""
    api = _make_api(tmp_path, monkeypatch)
    api.user, api.password = "alice", "s3cret"
    fake = FakeWslEndpoint("172.24.0.1")
    with _npmwsl_patches(fake):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
    assert api._wsl_written["npmwsl"] == \
        "http://alice:s3cret@172.24.0.1:7897"

    raw = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    on_disk = raw["wsl_written"]["npmwsl"]
    assert on_disk.startswith("DPAPI:")
    assert "alice" not in on_disk
    assert "s3cret" not in on_disk

    api2 = _make_api(tmp_path, monkeypatch)
    assert api2._wsl_written["npmwsl"] == \
        "http://alice:s3cret@172.24.0.1:7897"


class FakeWinEndpoint:
    """Blanket unset like the real npm/pip config delete: any disable removes
    whatever is configured, ours or not — which is exactly why quit-clear
    must gate on ownership before calling the setter."""

    def __init__(self, url=None):
        self.url = url
        self.disable_calls = 0

    def get(self):
        return self.url or pm.UNSET

    def setter(self, host, port, enable=True, scheme="http",
               user="", password=""):
        if not enable:
            self.disable_calls += 1
            self.url = None
        else:
            self.url = pm.build_proxy_url(host, port, scheme, user, password)
        return True


def test_quit_clear_skips_user_configured_npm(tmp_path, monkeypatch):
    """退出清理只关本工具写入的端点：用户手工配置的 npm 代理（无暂存、无
    记录、不等于 applied）不得被删除——"无记录一律当用户状态"契约必须
    覆盖退出清理路径（此前对 9 个端点无条件 disable，按检测值盲删）。"""
    api = _make_api(tmp_path, monkeypatch)
    npm = FakeWinEndpoint("http://10.0.0.8:10809")
    with patch("js_api.pm.get_ps_status", return_value=pm.UNSET),          patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET),          patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET),          patch("js_api.pm.get_npm_proxy", side_effect=npm.get),          patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET),          patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET),          patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET),          patch("js_api.pm.set_ps_proxy", return_value=True),          patch("js_api.pm.set_sys_proxy", return_value=True),          patch("js_api.pm.set_git_proxy", return_value=True),          patch("js_api.pm.set_wsl_proxy", return_value=True),          patch("js_api.pm.set_npm_proxy", side_effect=npm.setter),          patch("js_api.pm.set_pip_proxy", return_value=True),          patch("js_api.pm.set_npm_proxy_wsl", return_value=True),          patch("js_api.pm.set_pip_proxy_wsl", return_value=True),          patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}):
        assert api.clear_all_endpoints() is True
    assert npm.url == "http://10.0.0.8:10809"
    assert npm.disable_calls == 0


def test_quit_clear_still_disables_recorded_npmwsl(tmp_path, monkeypatch):
    """有"最后写入 URL"记录的端点仍会被退出清理正常关闭并清掉记录。"""
    api = _make_api(tmp_path, monkeypatch)
    npmwsl = FakeWslEndpoint("172.24.0.1")
    with _npmwsl_patches(npmwsl),          patch("js_api.pm.get_ps_status", return_value=pm.UNSET),          patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET),          patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET),          patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET),          patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET),          patch("js_api.pm.set_ps_proxy", return_value=True),          patch("js_api.pm.set_sys_proxy", return_value=True),          patch("js_api.pm.set_git_proxy", return_value=True),          patch("js_api.pm.set_wsl_proxy", return_value=True),          patch("js_api.pm.set_npm_proxy", return_value=True),          patch("js_api.pm.set_pip_proxy", return_value=True),          patch("js_api.pm.set_pip_proxy_wsl", return_value=True),          patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}):
        assert api.do_toggle("npmwsl", True, "127.0.0.1", "7897")["ok"] is True
        assert npmwsl.url is not None
        assert api._wsl_written["npmwsl"]
        assert api.clear_all_endpoints() is True
    assert npmwsl.url is None
    assert api._wsl_written["npmwsl"] is None
