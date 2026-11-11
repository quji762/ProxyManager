"""switch_port: only endpoints matching the managed URL are rewritten."""

import json
from contextlib import ExitStack
from unittest.mock import patch

import config_store
import proxy_manager as pm
from js_api import Api


def _disk_cfg(tmp_path):
    return json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))


def test_switch_port_phase2_failure_restores_old_applied(tmp_path, monkeypatch):
    """Phase-2 enable failure must not leave endpoints OFF with applied_* moved."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    managed = "http://127.0.0.1:7897"
    enables = []

    def set_ps(host, port, on, **kw):
        enables.append((host, str(port), on))
        if on and str(port) == "7890":
            return "failed to bind"
        return True

    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value="未设置"), \
         patch("js_api.pm.get_git_proxy_win", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value="未设置"), \
         patch.object(api, "refresh_item", return_value={"on": False, "git": "未设置"}), \
         patch("js_api.pm.set_ps_proxy", side_effect=set_ps), \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"):
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is False
    assert r.get("error_key") == "errSwitchPartial"
    assert api.applied_port == "7897"
    assert api.port == "7897"
    assert ( "127.0.0.1", "7897", True) in enables


def test_switch_port_noop_when_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    r = api.switch_port("7897", "127.0.0.1")
    assert r["ok"] is True
    assert r["applied"] == []


def test_switch_port_rewrites_only_ours(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme = "http"
    managed = "http://127.0.0.1:7897"
    calls = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return True
        return _fn

    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value="http://other:9"), \
         patch("js_api.pm.get_git_proxy_win", return_value=managed), \
         patch("js_api.pm.get_npm_proxy", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value="未设置"), \
         patch.object(api, "refresh_item", return_value={"on": False, "git": "未设置"}), \
         patch("js_api.pm.set_ps_proxy", side_effect=record("ps")), \
         patch("js_api.pm.set_sys_proxy", side_effect=record("sys")), \
         patch("js_api.pm.set_git_proxy", side_effect=record("git")), \
         patch("js_api.pm.set_wsl_proxy", side_effect=record("wsl")), \
         patch("js_api.pm.set_npm_proxy", side_effect=record("npm")), \
         patch("js_api.pm.set_pip_proxy", side_effect=record("pip")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=record("npmwsl")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=record("pipwsl")), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"):
        r = api.switch_port("7890", "127.0.0.1")

    names = [c[0] for c in calls]
    assert r["ok"] is True
    assert "ps" in names
    assert "git" in names
    assert "sys" not in names  # foreign URL must survive
    assert api.port == "7890"


def test_switch_port_migrates_after_unapplied_save_config(tmp_path, monkeypatch):
    """Regression: persistSoon saves the not-yet-applied form address via
    save_config, which used to poison the cache switch_port compares against
    — endpoints stayed on the old port while the UI reported success.
    switch_port must match against the last APPLIED address instead."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    # The user typed 7890 into the form and persistSoon already saved it.
    assert api.save_config({"host": "127.0.0.1", "port": "7890"}) is True
    assert api.port == "7890"
    managed = "http://127.0.0.1:7897"
    calls = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return True
        return _fn

    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value="http://other:9"), \
         patch("js_api.pm.get_git_proxy_win", return_value=managed), \
         patch("js_api.pm.get_npm_proxy", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value="未设置"), \
         patch.object(api, "refresh_item", return_value={"on": False, "git": "未设置"}), \
         patch("js_api.pm.set_ps_proxy", side_effect=record("ps")), \
         patch("js_api.pm.set_sys_proxy", side_effect=record("sys")), \
         patch("js_api.pm.set_git_proxy", side_effect=record("git")), \
         patch("js_api.pm.set_wsl_proxy", side_effect=record("wsl")), \
         patch("js_api.pm.set_npm_proxy", side_effect=record("npm")), \
         patch("js_api.pm.set_pip_proxy", side_effect=record("pip")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=record("npmwsl")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=record("pipwsl")), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"):
        r = api.switch_port("7890", "127.0.0.1")

    names = [c[0] for c in calls]
    assert r["ok"] is True
    assert "ps" in names       # endpoint on the OLD applied port must migrate
    assert "git" in names
    assert api.port == "7890"
    assert api.applied_port == "7890"


def test_applied_survives_unapplied_save_and_reload(tmp_path, monkeypatch):
    """persistSoon may save a typed-but-not-applied port. After restart,
    applied_* must still be the last address actually written to endpoints
    so switch_port can migrate them."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    assert api.save_config({"host": "127.0.0.1", "port": "7890"}) is True
    assert api.port == "7890"
    assert api.applied_port == "7897"

    with patch("proxy_manager.get_net_counters", return_value=None):
        api2 = Api()
    assert api2.port == "7890"
    assert api2.applied_port == "7897"
    assert api2.applied_host == "127.0.0.1"
    assert api2.applied_kw["scheme"] == "http"


def test_switch_port_after_reload_migrates_old_applied(tmp_path, monkeypatch):
    """Reload with form=7890 / applied=7897: switching to 7890 must still
    treat endpoints on 7897 as ours."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    assert api.save_config({"host": "127.0.0.1", "port": "7890"}) is True

    with patch("proxy_manager.get_net_counters", return_value=None):
        api2 = Api()
    managed = "http://127.0.0.1:7897"
    calls = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            return True
        return _fn

    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value="未设置"), \
         patch("js_api.pm.get_git_proxy_win", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy", return_value="未设置"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value="未设置"), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value="未设置"), \
         patch.object(api2, "refresh_item",
                      return_value={"on": False, "git": "未设置"}), \
         patch("js_api.pm.set_ps_proxy", side_effect=record("ps")), \
         patch("js_api.pm.set_sys_proxy", side_effect=record("sys")), \
         patch("js_api.pm.set_git_proxy", side_effect=record("git")), \
         patch("js_api.pm.set_wsl_proxy", side_effect=record("wsl")), \
         patch("js_api.pm.set_npm_proxy", side_effect=record("npm")), \
         patch("js_api.pm.set_pip_proxy", side_effect=record("pip")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=record("npmwsl")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=record("pipwsl")), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"):
        r = api2.switch_port("7890", "127.0.0.1")

    names = [c[0] for c in calls]
    assert r["ok"] is True
    assert "ps" in names
    assert api2.applied_port == "7890"


def test_fresh_config_does_not_persist_form_as_applied(tmp_path, monkeypatch):
    """No applied record must not fall back to the form address: a plain
    save_config must not write it, _ours_proxy_urls must claim nothing, and
    quit-clear must not disable a user proxy that merely shares the address.
    Only a successful enable creates the record."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    assert api.applied_host == ""
    assert api._ours_proxy_urls("sys") == set()

    assert api.save_config({"mode": "dark"}) is True
    assert _disk_cfg(tmp_path).get("applied") is None

    # A user-configured system proxy at the same address is NOT ours.
    with patch("js_api.pm.get_sys_proxy", return_value="http://127.0.0.1:7897"):
        assert api._quit_clear_owns("sys") is False

    # get_ps_env_snapshot must be patched too: _stash_previous reads the raw
    # per-name store for the ps branch, so without it this test reads the
    # machine's real HKCU\Environment (it "passes" here only because this box
    # happens to have no ps proxy variables) — same standard as
    # tests/test_stash_restore.py's FakePsEnv.snapshot sites.
    with patch("js_api.pm.get_ps_proxy_vars",
               return_value={"HTTP_PROXY": pm.UNSET,
                             "HTTPS_PROXY": pm.UNSET,
                             "ALL_PROXY": pm.UNSET}), \
         patch("js_api.pm.get_ps_env_snapshot",
               return_value=dict.fromkeys(pm.PS_ENV_NAMES)), \
         patch("js_api.pm.get_ps_no_proxy", return_value=""), \
         patch("js_api.pm.set_ps_proxy", return_value=True):
        r = api.do_toggle("ps", True, "127.0.0.1", "7897")
    assert r["ok"] is True

    raw = _disk_cfg(tmp_path)
    assert raw["applied"]["host"] == "127.0.0.1"
    assert raw["applied"]["port"] == "7897"
    with patch("js_api.pm.get_sys_proxy", return_value="http://127.0.0.1:7897"):
        assert api._quit_clear_owns("sys") is True


def test_switch_port_without_applied_record_claims_nothing(tmp_path, monkeypatch):
    """Fresh config (never wrote an endpoint): switch_port only syncs the form
    address — nothing is enabled, nothing is claimed, no applied record is
    fabricated on disk."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    with patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}):
        r = api.switch_port("7890")

    assert r == {"ok": True, "applied": []}
    assert _disk_cfg(tmp_path).get("applied") is None
    assert api.applied_host == ""


def test_switch_port_no_applied_keeps_user_proxy_at_form_address(tmp_path,
                                                                 monkeypatch):
    """契约A2 回归：没有 applied 记录时，绝不能拿表单地址回填 managed_url。
    用户手工配置了一个恰好等于表单地址的代理（127.0.0.1:7890），端口切换
    必须只同步表单，不 disable/re-enable 该端点、不升格 applied_*——否则
    退出清理会把用户自己的代理删掉。修复前 old_h 回退到 new_host，
    get_ps_status()==managed_url 成立，ps 会被误判为本工具所有。"""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    assert api.applied_host == ""
    api.host, api.port = "127.0.0.1", "7890"
    calls = []

    def record(name):
        def _fn(*args, **kwargs):
            calls.append(name)
            return True
        return _fn

    user_url = "http://127.0.0.1:7890"
    with patch("js_api.pm.get_ps_status", return_value=user_url), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_ps_proxy", side_effect=record("ps")), \
         patch("js_api.pm.set_sys_proxy", side_effect=record("sys")), \
         patch("js_api.pm.set_git_proxy", side_effect=record("git")), \
         patch("js_api.pm.set_wsl_proxy", side_effect=record("wsl")), \
         patch("js_api.pm.set_npm_proxy", side_effect=record("npm")), \
         patch("js_api.pm.set_pip_proxy", side_effect=record("pip")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=record("npmwsl")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=record("pipwsl")):
        r = api.switch_port("7890", "127.0.0.1")

    assert r == {"ok": True, "applied": []}
    assert calls == []                       # 没有端点被 disable/re-enable
    assert api.applied_host == ""            # 没有伪造 applied 记录
    assert api.applied_port == ""
    assert _disk_cfg(tmp_path).get("applied") is None
    assert api.port == "7890"                # 表单地址照常同步


# ── rollback integrity: undo by the URL that is really in the file ───────────
def _rollback_api(tmp_path, monkeypatch, wsl_git):
    """Api wired for a NAT/mirrored WSL switch with gitwsl + pip on.
    `wsl_git` is the URL the WSL git config currently holds (the stale
    gateway form the tool wrote last time)."""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    api._wsl_written["gitwsl"] = wsl_git
    return api


def _rollback_patches(api, wsl_git, git_calls, set_git, mode="NAT",
                      pip_ret=True):
    """Patch set for the rollback tests. `pip_ret` may be a callable
    (host, port, on) → result so a test can fail one specific phase."""
    managed = "http://127.0.0.1:7897"
    return [
        patch("js_api.pm.get_ps_status", return_value=pm.UNSET),
        patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET),
        patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET),
        patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET),
        # pip is the second enabled endpoint: the one a test makes fail
        patch("js_api.pm.get_pip_proxy", return_value=managed),
        patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET),
        patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET),
        patch.object(api, "refresh_item", return_value={"on": False,
                                                       "git": wsl_git}),
        patch("js_api.pm.get_wsl_network_mode", return_value=mode),
        patch("js_api.pm.get_wsl_gateway_ip", return_value="172.24.0.1"),
        patch("js_api.pm.set_ps_proxy", return_value=True),
        patch("js_api.pm.set_sys_proxy", return_value=True),
        patch("js_api.pm.set_git_proxy",
              side_effect=lambda *a, **k: git_calls.append((a, k)) or set_git(*a, **k)),
        patch("js_api.pm.set_wsl_proxy", return_value=True),
        patch("js_api.pm.set_npm_proxy", return_value=True),
        patch("js_api.pm.set_npm_proxy_wsl", return_value=True),
        patch("js_api.pm.set_pip_proxy_wsl", return_value=True),
        patch("js_api.pm.set_pip_proxy", side_effect=pip_ret),
    ]


def test_switch_port_phase2_rollback_unsets_gateway_gitwsl_url(tmp_path,
                                                              monkeypatch):
    """NAT 场景，专抓「断言迁就实现」：第二阶段 pip 启用失败，gitwsl 的回滚必须
    按文件里真实的 URL——本轮刚写进去的网关形态——卸除。_git_wsl_unset_cmd 是
    --fixed-value 卸载，URL 不相等就静默 no-op，用户已经放弃的新端口会继续留在
    WSL 的 git config 里（裸传 new_host 就是这个后果）。"""
    api = _rollback_api(tmp_path, monkeypatch, "http://172.24.0.1:7897")
    git_calls = []

    def pip_setter(host, port, enable=True, **kw):
        return "pip boom" if (enable and str(port) == "7890") else True

    with ExitStack() as stack:
        for p in _rollback_patches(api, "http://172.24.0.1:7897", git_calls,
                                   lambda *a, **k: True, pip_ret=pip_setter):
            stack.enter_context(p)
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is False
    assert r.get("error_key") == "errSwitchPartial"
    assert api.applied_port == "7897"          # applied_* 不移动
    # 第二阶段回滚的那次卸载：用网关 URL，不是 127.0.0.1
    # （gitwsl 两阶段各调用一次，回滚卸载＝新端口上的 disable）
    rollback_unsets = [c for c in git_calls
                       if c[0][1] == "7890" and c[0][2] is False]
    assert rollback_unsets == [
        (("172.24.0.1", "7890", False),
         {"use_wsl": True, "scheme": "http", "user": "", "password": ""})]
    # 卸除成功：会话记录清空；"最后写入 URL"记录指向回滚后文件里真实存在的
    # 旧网关 URL（不是被放弃的新端口）——清空记录会让 _is_managed_wsl_url
    # 判否、quit-clear 漏掉这个端点，陈旧代理永久残留。
    assert api._git_managed["gitwsl"] is None
    assert api._wsl_written["gitwsl"] == "http://172.24.0.1:7897"


def test_switch_port_phase2_rollback_unsets_new_gitwsl_url_mirrored(tmp_path,
                                                                   monkeypatch):
    """Mirrored 模式不做网关改写：文件里就是 new_host:new_port，回滚也按它卸。"""
    api = _rollback_api(tmp_path, monkeypatch, "http://127.0.0.1:7897")
    git_calls = []

    def pip_setter(host, port, enable=True, **kw):
        return "pip boom" if (enable and str(port) == "7890") else True

    with ExitStack() as stack:
        for p in _rollback_patches(api, "http://127.0.0.1:7897", git_calls,
                                   lambda *a, **k: True, mode="Mirrored",
                                   pip_ret=pip_setter):
            stack.enter_context(p)
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is False
    rollback_unsets = [c for c in git_calls
                       if c[0][1] == "7890" and c[0][2] is False]
    assert rollback_unsets == [
        (("127.0.0.1", "7890", False),
         {"use_wsl": True, "scheme": "http", "user": "", "password": ""})]
    assert api._wsl_written["gitwsl"] == "http://127.0.0.1:7897"


def test_switch_port_phase1_rollback_uses_ctx_url(tmp_path, monkeypatch):
    """第一阶段（任何写入之前）的 gitwsl 卸载必须仍用 ctx["gitwsl_unset"]——
    写入前文件里真实存在的 URL，可能是 _adopt_parsed 采用出来的陈旧网关形态。
    回归：改成 new_host 会让 --fixed-value 卸载静默 no-op，旧端口留在文件里。

    注：sys 在 _SWITCH_TARGETS 里排在 gitwsl 之前，它失败时 gitwsl 还没被卸；
    所以这里让排在 gitwsl 之后的 pip 失败，才能真正观察到第一阶段用的 URL。"""
    api = _rollback_api(tmp_path, monkeypatch, "http://172.24.0.1:7897")
    git_calls = []

    def pip_setter(host, port, enable=True, **kw):
        return "pip boom" if not enable else True

    with ExitStack() as stack:
        for p in _rollback_patches(api, "http://172.24.0.1:7897", git_calls,
                                   lambda *a, **k: True, pip_ret=pip_setter):
            stack.enter_context(p)
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is False
    assert r["applied"] == []
    assert "pip" in r["error"]
    # 第一次 set_git_proxy＝第一阶段卸载：ctx 里采用出来的陈旧网关 URL
    assert git_calls[0][0] == ("172.24.0.1", "7897", False)
    assert git_calls[0][1]["use_wsl"] is True


def test_switch_port_reports_rollback_failures_and_still_on(tmp_path,
                                                            monkeypatch):
    """第二阶段 npm 启用失败 + pip 的回滚卸载也失败：错误必须同时含原始失败与
    回滚失败，applied 报出仍持新地址的端点，applied_* 停在旧地址。"""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    managed = "http://127.0.0.1:7897"
    calls = []

    def rec(name, fail=None):
        def _fn(*args, **kwargs):
            calls.append((name, args, kwargs))
            if fail is not None and fail(args, kwargs):
                return "boom"
            return True
        return _fn

    with patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=managed), \
         patch("js_api.pm.get_pip_proxy", return_value=managed), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_ps_proxy", return_value=True), \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy",
               side_effect=rec("npm", lambda a, k: a[2] and str(a[1]) == "7890")), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy",
               side_effect=rec("pip", lambda a, k: (not a[2])
                               and str(a[1]) == "7890")):
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is False
    assert r.get("error_key") == "errSwitchPartial"
    assert "npm" in r["error"]                 # 原始失败
    assert "回滚失败 pip" in r["error"]         # 回滚自身的失败
    assert r["applied"] == ["pip"]             # 仍在新地址上
    assert api.applied_port == "7897"          # applied_* 停在旧地址
    assert api.port == "7897"


def test_switch_port_phase1_rollback_failure_is_reported(tmp_path, monkeypatch):
    """第一阶段 sys 关闭失败 + ps 的回滚重新启用也失败：两条都要报出来。"""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    managed = "http://127.0.0.1:7897"
    sys_url = pm.wininet_url("127.0.0.1", "7897", "http")

    def set_ps(host, port, enable=True, **kw):
        return "ps rollback boom" if enable else True

    with patch("js_api.pm.get_ps_status", return_value=managed), \
         patch("js_api.pm.get_sys_proxy", return_value=sys_url), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_ps_proxy", side_effect=set_ps), \
         patch("js_api.pm.set_sys_proxy", return_value="access denied"), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True):
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is False
    assert r["applied"] == []
    assert r["note"] is None
    assert "access denied" in r["error"]            # 原始失败
    assert "回滚失败 ps" in r["error"]               # 回滚自身的失败
    assert api.applied_port == "7897"


def test_switch_port_success_carries_note_parts(tmp_path, monkeypatch):
    """成功返回不再带 note_key（ui/i18n.js 的 switched 被重复定义覆盖过），
    改按 do_toggle 范式下发 note_parts，让 NAT 改写提示以 UI 语言渲染。"""
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        api = Api()
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}

    with patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item", return_value={"on": True,
                                                        "git": pm.UNSET}), \
         patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
         patch("js_api.pm.get_wsl_gateway_ip", return_value="172.24.0.1"), \
         patch("js_api.pm.set_ps_proxy", return_value=True), \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True):
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is True
    assert r["applied"] == ["wsl"]
    assert "note_key" not in r
    assert {"key": "natRewritten", "params": {}} in r["note_parts"]
    assert r["note"]


def test_switch_port_gitwsl_rollback_failure_keeps_records(tmp_path,
                                                           monkeypatch):
    """gitwsl 的回滚卸载失败：_git_managed/_wsl_written 必须保留，quit-clear
    才能继续定位并清掉这个端点（否则网关形态的代理永久残留）。"""
    api = _rollback_api(tmp_path, monkeypatch, "http://172.24.0.1:7897")
    git_calls = []

    def set_git(host, port, enable=True, use_wsl=False, **kw):
        # 只让「第二阶段回滚的那次卸载」失败（新端口 + disable）
        return "unset failed" if (not enable and str(port) == "7890") else True

    def pip_setter(host, port, enable=True, **kw):
        return "pip boom" if (enable and str(port) == "7890") else True

    with ExitStack() as stack:
        for p in _rollback_patches(api, "http://172.24.0.1:7897", git_calls,
                                   set_git, pip_ret=pip_setter):
            stack.enter_context(p)
        r = api.switch_port("7890", "127.0.0.1")

    assert r["ok"] is False
    assert "回滚失败 gitwsl" in r["error"]
    assert r["applied"] == ["gitwsl"]          # 仍在新地址上
    # 记录没被清掉：_is_managed_wsl_url 仍判 True，quit-clear 仍能清它
    assert api._git_managed["gitwsl"]
    assert api._wsl_written["gitwsl"]

