"""WSL scope narrowing ("apply to all user distros" → default distro only).

Distros that leave the scope keep whatever this tool wrote there, and the
narrowed scope hides them from every later read/write path — so the writes
must be removed at the moment the scope narrows, and a quit-clear under a
narrowed scope must still be able to sweep them. Only this tool's own
writes are ever touched: the shell block goes by MARKER, and git/npm/pip go
by the exact URL recorded in _wsl_written (no record = never written =
user state).

Everything runs against fakes — no real WSL, registry or endpoint is touched.
"""

from unittest.mock import patch

import pytest

import config_store
import proxy_manager as pm
from js_api import Api


@pytest.fixture(autouse=True)
def _restore_wsl_scope(monkeypatch):
    """模块级 scope 与枚举缓存必须在测试之间还原。

    save_config 收窄 scope 时会调 pm.set_wsl_apply_all（模块级全局）。不还原的
    话，收窄的 scope 会泄漏到本文件之后的测试里——例如 tests/
    test_wsl_written_record.py 的 quit-clear 用例会因此走进
    wsl_sweep_targets()，跑一次真实的 `wsl -l -q`。"""
    monkeypatch.setattr(pm, "_wsl_apply_all", pm._wsl_apply_all)
    monkeypatch.setattr(pm, "_wsl_distros_cache", None)
    yield


# In-distro files, keyed by the $HOME form _bash_path() produces. Each holds
# the URL this tool is recorded to have written, so a surgical disable has
# something real to remove.
WSL_FILES = {
    "$HOME/.npmrc": b"proxy=http://172.24.0.1:7897\n"
                    b"https-proxy=http://172.24.0.1:7897\n",
    "$HOME/.config/pip/pip.conf": b"[global]\nproxy = http://172.24.0.1:7897\n",
    "$HOME/.pip/pip.conf": b"[global]\nproxy = http://172.24.0.1:7897\n",
}

BLOCK = (b"# >>> proxy-manager begin >>>\n"
         b'export HTTP_PROXY="http://172.24.0.1:7897"\n'
         b"# <<< proxy-manager end <<<\n")

RECORD = "http://172.24.0.1:7897"


class FakeWsl:
    """Records every wsl.exe command and answers the file reads the WSL-side
    setters make, per distro. `block_distros` still carries a managed block
    (what wsl_managed_block_distros reads back)."""

    def __init__(self, files=None, block_distros=()):
        self.ran = []
        self.files = dict(files or {})
        self.block_distros = set(block_distros)

    def __call__(self, cmd, **kw):
        self.ran.append(list(cmd))
        script = cmd[-1] if cmd else ""
        distro = cmd[2] if len(cmd) > 3 and cmd[1] == "-d" else ""

        class R:
            returncode = 0
            stderr = b""
            stdout = b""

        # 读回命令由 _wsl_block_read_cmd() 生成，其中的标记经过 re.escape，
        # 所以脚本以 `sed -n '/\#\ >>>`（而非字面 `sed -n '/# >>>`）开头；
        # `sed -n` 是读命令独有前缀（写/禁用走 apply_posix/awk，读文件走 if [ -f ）。
        if script.startswith("sed -n '/"):
            R.stdout = BLOCK if distro in self.block_distros else b""
        elif script.startswith("dir=$(dirname"):
            pass                       # atomic push → success
        elif script.startswith('if [ -f "'):
            data = self.files.get(script.split('"')[1])
            if data is None:
                R.returncode = 3       # file absent
            else:
                R.stdout = data
        return R()

    def distros_of(self, cmds):
        return [c[2] for c in cmds if len(c) > 3 and c[1] == "-d"]

    def scripts_for(self, distro):
        return [c[-1] for c in self.ran
                if len(c) > 3 and c[1] == "-d" and c[2] == distro]


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    monkeypatch.setattr(config_store, "APP_DIR", str(tmp_path))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def _scoped_api(tmp_path, monkeypatch):
    api = _make_api(tmp_path, monkeypatch)
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    api._wsl_written.update({"gitwsl": RECORD, "npmwsl": RECORD, "pipwsl": RECORD})
    return api


def test_scope_narrow_clears_only_out_of_scope_distros(tmp_path, monkeypatch):
    """all → default: every leaving distro loses the block AND the git/npm/pip
    URLs this tool wrote; the distro that stays in scope is never touched."""
    api = _scoped_api(tmp_path, monkeypatch)
    fake = FakeWsl(WSL_FILES)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_default_distro_name", return_value="Ubuntu"), \
         patch.object(pm, "wsl_recorded_url_residue", return_value=[]), \
         patch.object(pm.subprocess, "run", fake):
        r = api.save_config({"wsl_all_distros": False})

    assert r["ok"] is True
    cleanup = r["wsl_cleanup"]
    assert cleanup["attempted"] == ["Debian"]
    assert cleanup["left"] == []
    assert cleanup["errors"] == []
    assert cleanup["failed"] == []
    # Ubuntu（仍在 scope 内）一个命令都不该收到
    assert fake.distros_of(fake.ran) == ["Debian"] * len(fake.ran)
    joined = "\n".join(fake.scripts_for("Debian"))
    assert "apply_posix" in joined                     # shell 块按标记删除
    assert "--fixed-value" in joined and "http.proxy" in joined   # git 卸载
    assert ".npmrc" in joined                          # npm 卸载
    assert "pip.conf" in joined                        # pip 卸载（含 legacy）
    # 新 scope 已生效
    assert api._wsl_apply_all is False
    assert pm.get_wsl_apply_all() is False


def test_scope_narrow_skips_git_npm_pip_without_record(tmp_path, monkeypatch):
    """无 _wsl_written 记录＝本工具从未写入＝用户状态：只删 shell 块，
    git/npm/pip 一个命令都不发。"""
    api = _make_api(tmp_path, monkeypatch)
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    fake = FakeWsl(WSL_FILES)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_default_distro_name", return_value="Ubuntu"), \
         patch.object(pm.subprocess, "run", fake):
        r = api.save_config({"wsl_all_distros": False})

    scripts = [c[-1] for c in fake.ran]
    assert any("apply_posix" in s for s in scripts)        # 块删除发生了
    assert not any("git config" in s for s in scripts)     # git 不碰
    assert not any(".npmrc" in s for s in scripts)         # npm 不碰
    assert not any("pip.conf" in s for s in scripts)       # pip 不碰
    assert r["wsl_cleanup"]["attempted"] == ["Debian"]
    assert r["wsl_cleanup"]["left"] == []


def test_scope_widen_runs_no_cleanup(tmp_path, monkeypatch):
    """default → all 是放宽：没有任何发行版离开 scope，一次 wsl.exe 都不该
    多跑（用户新装的发行版由随后的读取自然看到）。"""
    api = _scoped_api(tmp_path, monkeypatch)
    fake = FakeWsl(WSL_FILES)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_default_distro_name", return_value="Ubuntu"), \
         patch.object(pm.subprocess, "run", fake):
        assert api.save_config({"wsl_all_distros": False})["ok"] is True
        before = list(fake.ran)
        assert api.save_config({"wsl_all_distros": True}) is True
        assert fake.ran == before
    assert api._wsl_apply_all is True


def test_scope_narrow_no_cleanup_when_default_unknown(tmp_path, monkeypatch):
    """默认发行版未知（WSL 不可用）时绝不猜：不清理、返回值保持 True。"""
    api = _scoped_api(tmp_path, monkeypatch)
    fake = FakeWsl(WSL_FILES)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_default_distro_name", return_value=""), \
         patch.object(pm.subprocess, "run", fake):
        r = api.save_config({"wsl_all_distros": False})

    assert r is True                 # 不清理 → 返回值逐字不变
    assert fake.ran == []            # 探测之外没有任何 setter 调用
    # scope 本身仍然切过去了（只是没人可清）
    assert api._wsl_apply_all is False
    assert api._wsl_written["gitwsl"] == RECORD


def test_quit_clear_sweeps_every_distro_when_scope_narrowed(tmp_path,
                                                            monkeypatch):
    """退出清理在收窄过的 scope 下仍要扫到离开 scope 的发行版：三个 WSL 侧
    端点都以记录里的 URL、enable=False、distros=全部发行版 卸载。"""
    api = _scoped_api(tmp_path, monkeypatch)
    api._wsl_apply_all = False
    # monkeypatch 而不是 pm.set_wsl_apply_all：模块级 scope 必须在测试结束后
    # 还原，否则收窄的 scope 会泄漏到后面的 quit-clear 测试里，让
    # wsl_sweep_targets() 去跑真实的 wsl -l -q。
    monkeypatch.setattr(pm, "_wsl_apply_all", False)
    git, npm, pip = [], [], []
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_managed_block_distros", return_value=[]), \
         patch.object(pm, "wsl_recorded_url_residue", return_value=[]), \
         patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_ps_proxy", return_value=True), \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy",
               side_effect=lambda *a, **k: git.append((a, k)) or True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl",
               side_effect=lambda *a, **k: npm.append((a, k)) or True), \
         patch("js_api.pm.set_pip_proxy_wsl",
               side_effect=lambda *a, **k: pip.append((a, k)) or True):
        assert api.clear_all_endpoints() is True

    wsl_git = [(a, k) for a, k in git if k.get("use_wsl")]
    assert wsl_git == [(("172.24.0.1", "7897", False),
                        {"use_wsl": True, "distros": ["Ubuntu", "Debian"],
                         "scheme": "http", "user": "", "password": ""})]
    assert npm == [(("172.24.0.1", "7897", False),
                    {"distros": ["Ubuntu", "Debian"], "scheme": "http",
                     "user": "", "password": ""})]
    assert pip == [(("172.24.0.1", "7897", False),
                    {"distros": ["Ubuntu", "Debian"], "scheme": "http",
                     "user": "", "password": ""})]
    # 扫干净了：记录交给逐端点循环前的清理一并清掉
    assert api._wsl_written["npmwsl"] is None


def test_quit_clear_keeps_records_when_sweep_leaves_block(tmp_path,
                                                          monkeypatch):
    """块没删干净（读回仍有托管块）时保留记录：随后的逐端点循环至少还能清
    in-scope 发行版，quit-clear 也不会把"还写着我们 URL"的发行版遗忘。"""
    api = _scoped_api(tmp_path, monkeypatch)
    api._wsl_apply_all = False
    # monkeypatch 而不是 pm.set_wsl_apply_all：模块级 scope 必须在测试结束后
    # 还原，否则收窄的 scope 会泄漏到后面的 quit-clear 测试里，让
    # wsl_sweep_targets() 去跑真实的 wsl -l -q。
    monkeypatch.setattr(pm, "_wsl_apply_all", False)
    seen = {}
    real = api._clear_wsl_out_of_scope

    def spy(distros):
        res = real(distros)
        seen["res"] = res
        seen["written"] = dict(api._wsl_written)
        return res

    monkeypatch.setattr(api, "_clear_wsl_out_of_scope", spy)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_managed_block_distros", return_value=["Debian"]), \
         patch.object(pm, "wsl_recorded_url_residue", return_value=[]), \
         patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_ps_proxy", return_value=True), \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True):
        assert api.clear_all_endpoints() is True

    assert seen["res"]["left"] == ["Debian"]
    # 扫描阶段不清记录（逐端点循环是否清是另一回事）
    assert seen["written"] == {"gitwsl": RECORD, "npmwsl": RECORD,
                               "pipwsl": RECORD}


def test_setters_ignore_distros_when_none(monkeypatch):
    """既有调用方都不传 distros：四个 setter 仍按 _wsl_targets() 走，
    命令序列逐字不变。"""
    fake = FakeWsl(WSL_FILES)
    monkeypatch.setattr(pm, "_wsl_targets", lambda: ["X"])
    with patch.object(pm.subprocess, "run", fake):
        assert pm.set_wsl_proxy("127.0.0.1", "7897", False) is True
        assert pm.set_git_proxy("127.0.0.1", "7897", False,
                                use_wsl=True) is True
        assert pm.set_npm_proxy_wsl("127.0.0.1", "7897", False) is True
        assert pm.set_pip_proxy_wsl("127.0.0.1", "7897", False) is True

    assert fake.ran
    assert fake.distros_of(fake.ran) == ["X"] * len(fake.ran)


def test_wsl_managed_block_distros_reports_survivors():
    """读回验证：仍带托管块的发行版必须被报出来，否则一次失败的 rc 写入会被
    当成清理成功。"""
    fake = FakeWsl(WSL_FILES, block_distros={"Debian"})
    with patch.object(pm.subprocess, "run", fake):
        assert pm.wsl_managed_block_distros(["Ubuntu", "Debian"]) == ["Debian"]
        assert pm.wsl_managed_block_distros([]) == []
        # 空列表不发任何 wsl.exe
        assert len(fake.ran) == 2


def test_wsl_managed_block_distros_skips_unreadable_distro():
    """单个发行版读不动（超时/wsl 失败）只跳过该项，函数整体不抛。"""
    import subprocess

    def fake_run(cmd, **kw):
        if len(cmd) > 3 and cmd[1] == "-d" and cmd[2] == "Ubuntu":
            raise subprocess.TimeoutExpired(cmd, 15)

        class R:
            returncode = 0
            stderr = b""
            stdout = BLOCK
        return R()

    with patch.object(pm.subprocess, "run", fake_run):
        assert pm.wsl_managed_block_distros(["Ubuntu", "Debian"]) == ["Debian"]


def test_wsl_default_distro_name_unknown_on_failure():
    """默认发行版探不到（非零退出/超时/空输出）一律返回 ""：调用方必须把 ""
    当「未知」并跳过清理，绝不猜一个默认发行版。"""
    import subprocess
    from types import SimpleNamespace

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 15)

    with patch.object(pm.subprocess, "run", boom):
        assert pm.wsl_default_distro_name() == ""
    with patch.object(pm.subprocess, "run",
                      return_value=SimpleNamespace(returncode=1, stdout="")):
        assert pm.wsl_default_distro_name() == ""
    with patch.object(pm.subprocess, "run",
                      return_value=SimpleNamespace(returncode=0, stdout="  \n")):
        assert pm.wsl_default_distro_name() == ""
    with patch.object(pm.subprocess, "run",
                      return_value=SimpleNamespace(returncode=0,
                                                   stdout="Ubuntu\n")):
        assert pm.wsl_default_distro_name() == "Ubuntu"


# ── recorded-URL read-back (best-effort disable can lie) ──────────────────


def test_wsl_recorded_url_residue_detects_each_kind():
    """git/npm/pip 里仍写着记录 URL 的端点必须被逐个报出。"""
    url = "http://172.24.0.1:7897"

    def fake_run(cmd, **kw):
        script = cmd[-1]

        class R:
            returncode = 0
            stderr = b""
            stdout = b""

        if script.startswith("git config"):
            R.stdout = url + "\n"   # git getter 用 text=True，stdout 是 str
        elif ".npmrc" in script:
            R.stdout = f"proxy={url}\n".encode()
        elif "pip.conf" in script:
            R.stdout = f"[global]\nproxy = {url}\n".encode()
        return R()

    with patch.object(pm.subprocess, "run", fake_run):
        assert pm.wsl_recorded_url_residue(
            "D", git_url=url, npm_url=url, pip_url=url) == \
            ["gitwsl", "npmwsl", "pipwsl"]
        # 只查给了 URL 的端点：npm 残留不牵连 git/pip
        assert pm.wsl_recorded_url_residue("D", npm_url=url) == ["npmwsl"]


def test_wsl_recorded_url_residue_clean_and_unreadable():
    """全部卸干净 → []；发行版读不动 → None（清理未验证，绝不能当干净）；
    没给任何 URL → 一个 wsl.exe 都不发。"""
    import subprocess

    def clean_run(cmd, **kw):
        script = cmd[-1]

        class R:
            returncode = 1 if script.startswith("git config") else 3
            stderr = b""
            stdout = b""

        return R()

    ran = []

    def counting_run(cmd, **kw):
        ran.append(cmd)
        return clean_run(cmd, **kw)

    with patch.object(pm.subprocess, "run", counting_run):
        assert pm.wsl_recorded_url_residue(
            "D", git_url="http://x:1", npm_url="http://x:1",
            pip_url="http://x:1") == []
        assert ran
        ran.clear()
        assert pm.wsl_recorded_url_residue("D") == []
        assert ran == []

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 15)

    with patch.object(pm.subprocess, "run", boom):
        assert pm.wsl_recorded_url_residue("D", git_url="http://x:1") is None


def test_scope_narrow_reports_residue_and_failed(tmp_path, monkeypatch):
    """disable 报成功但读回仍有记录 URL：left/failed 必须报出来，
    save_config 路径不清记录（记录留给下次清理认领）。"""
    api = _scoped_api(tmp_path, monkeypatch)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_default_distro_name", return_value="Ubuntu"), \
         patch.object(pm, "set_wsl_proxy", return_value=True), \
         patch.object(pm, "set_git_proxy", return_value=True), \
         patch.object(pm, "set_npm_proxy_wsl", return_value=True), \
         patch.object(pm, "set_pip_proxy_wsl", return_value=True), \
         patch.object(pm, "wsl_managed_block_distros", return_value=[]), \
         patch.object(pm, "wsl_recorded_url_residue",
                      return_value=["npmwsl"]) as residue:
        r = api.save_config({"wsl_all_distros": False})

    cleanup = r["wsl_cleanup"]
    assert cleanup["errors"] == []
    assert cleanup["left"] == ["Debian"]
    assert cleanup["failed"] == ["npmwsl"]
    assert api._wsl_written["npmwsl"] == RECORD
    residue.assert_called_once_with("Debian", git_url=RECORD,
                                    npm_url=RECORD, pip_url=RECORD)


def test_scope_narrow_unreadable_distro_keeps_all_records(tmp_path,
                                                          monkeypatch):
    """读不动的发行版＝清理未验证：三个端点全标 failed，记录全保留。"""
    api = _scoped_api(tmp_path, monkeypatch)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_default_distro_name", return_value="Ubuntu"), \
         patch.object(pm, "set_wsl_proxy", return_value=True), \
         patch.object(pm, "set_git_proxy", return_value=True), \
         patch.object(pm, "set_npm_proxy_wsl", return_value=True), \
         patch.object(pm, "set_pip_proxy_wsl", return_value=True), \
         patch.object(pm, "wsl_managed_block_distros", return_value=[]), \
         patch.object(pm, "wsl_recorded_url_residue", return_value=None):
        r = api.save_config({"wsl_all_distros": False})

    cleanup = r["wsl_cleanup"]
    assert cleanup["left"] == ["Debian"]
    assert cleanup["failed"] == ["gitwsl", "npmwsl", "pipwsl"]
    assert api._wsl_written == {"gitwsl": RECORD, "npmwsl": RECORD,
                                "pipwsl": RECORD}


def test_quit_clear_keeps_record_despite_in_scope_disable_success(
        tmp_path, monkeypatch):
    """sweep 在 Debian 上没清掉 gitwsl，但随后 in-scope（默认发行版）的
    do_toggle 成功并清了记录——记录必须在循环后被装回，否则 Debian 里残留
    的 URL 永久失去归属。npm/pip 已清干净，记录照常清掉。"""
    api = _scoped_api(tmp_path, monkeypatch)
    api._wsl_apply_all = False
    monkeypatch.setattr(pm, "_wsl_apply_all", False)
    with patch.object(pm, "_wsl_distros", return_value=["Ubuntu", "Debian"]), \
         patch.object(pm, "wsl_managed_block_distros", return_value=[]), \
         patch.object(pm, "wsl_recorded_url_residue",
                      side_effect=lambda d, **k: ["gitwsl"] if d == "Debian"
                      else []), \
         patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}), \
         patch("js_api.pm.set_ps_proxy", return_value=True), \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True):
        assert api.clear_all_endpoints() is True

    assert api._wsl_written["gitwsl"] == RECORD
    assert api._git_managed["gitwsl"] == RECORD
    assert api._wsl_written["npmwsl"] is None
    assert api._wsl_written["pipwsl"] is None


# ── timeout must not skip the multi-distro rollback ───────────────────────


def _stateful_wsl_files(initial):
    """Fake wsl.exe over per-distro file state: pulls read the current bytes,
    pushes replace them; `timeouts` maps (distro, phase) to TimeoutExpired.
    phase: 'pull' / 'push'."""
    files = {d: data for d, data in initial.items()}
    pushes = []

    def run(cmd, **kw):
        import subprocess
        d = cmd[2] if len(cmd) > 3 and cmd[1] == "-d" else ""
        script = cmd[-1]

        class R:
            returncode = 0
            stderr = b""
            stdout = b""

        if script.startswith('if [ -f "'):
            if ("pull", d) in run.timeouts:
                raise subprocess.TimeoutExpired(cmd, 15)
            data = files.get(d)
            if data is None:
                R.returncode = 3
            else:
                R.stdout = data
        elif script.startswith("dir=$(dirname"):
            if ("push", d) in run.timeouts:
                raise subprocess.TimeoutExpired(cmd, 15)
            files[d] = kw.get("input")
            pushes.append(d)
        return R()

    run.timeouts = set()
    run.files = files
    run.pushes = pushes
    return run


def test_npm_wsl_timeout_rolls_back_written_distro(monkeypatch):
    """B 的快照读超时（TimeoutExpired 不是 OSError）必须触发对 A 的回滚，
    且回滚写回用户原值——旧实现只捕 OSError，超时直接跳过回滚循环。"""
    monkeypatch.setattr(pm, "_wsl_targets", lambda: ["A", "B"])
    user_file = b"proxy=http://corp:8080\n"
    fake = _stateful_wsl_files({"A": user_file})
    fake.timeouts.add(("pull", "B"))
    with patch.object(pm.subprocess, "run", fake):
        ret = pm.set_npm_proxy_wsl("127.0.0.1", "7897", True)
    assert ret is not True
    assert fake.files["A"] == user_file     # 用户原值逐字恢复
    # 一次启用写入 + 回滚按键写回；所有写入都只发生在 A 上
    assert len(fake.pushes) >= 2 and all(d == "A" for d in fake.pushes)


def test_pip_wsl_timeout_rolls_back_written_distro(monkeypatch):
    """pip 同 npm：超时进 errs，已写入的发行版回滚到快照。"""
    monkeypatch.setattr(pm, "_wsl_targets", lambda: ["A", "B"])
    user_file = b"[global]\nproxy = http://corp:8080\n"
    fake = _stateful_wsl_files({"A": user_file})
    fake.timeouts.add(("pull", "B"))
    with patch.object(pm.subprocess, "run", fake):
        ret = pm.set_pip_proxy_wsl("127.0.0.1", "7897", True)
    assert ret is not True
    assert fake.files["A"] == user_file


def test_npm_wsl_rollback_continues_after_timeout(monkeypatch):
    """回滚循环里 A 超时不能中断 B 的回滚（旧实现 except OSError 会）。"""
    monkeypatch.setattr(pm, "_wsl_targets", lambda: ["A", "B", "C"])
    user_file = b"proxy=http://corp:8080\n"
    fake = _stateful_wsl_files({"A": user_file, "B": user_file})
    fake.timeouts.add(("pull", "C"))     # C 触发回滚
    fake.timeouts.add(("push", "A"))     # A 的回滚写入超时
    with patch.object(pm.subprocess, "run", fake):
        ret = pm.set_npm_proxy_wsl("127.0.0.1", "7897", True)
    assert ret is not True
    assert fake.files["B"] == user_file  # B 仍然被回滚
    assert "B" in fake.pushes


