"""WSL status reads: multi-distro aggregation and write-failure decoding."""

from types import SimpleNamespace

import proxy_manager as pm


def test_wsl_write_failure_decodes_bytes_stderr(monkeypatch):
    monkeypatch.setattr(pm, "wsl_available", lambda: True)
    r = SimpleNamespace(stderr=b"Permission denied\n")
    msg = pm._wsl_write_failure(r, "")
    assert "b'" not in msg
    assert "Permission denied" in msg


def test_wsl_write_failure_empty_bytes_uses_fallback(monkeypatch):
    monkeypatch.setattr(pm, "wsl_available", lambda: True)
    r = SimpleNamespace(stderr=b"")
    msg = pm._wsl_write_failure(r, "")
    assert "b'" not in msg
    assert "写入 WSL 代理配置失败" in msg


def test_get_wsl_ip_and_block_reads_non_default_distro(monkeypatch):
    """Writes walk every user distro; status must not hide a block that
    only exists on a non-default distro."""
    monkeypatch.setattr(pm, "_wsl_targets", lambda: ["Ubuntu", "Debian"])

    def fake_one(distro=""):
        if distro == "Ubuntu":
            return ("172.24.1.1", "", pm.UNSET)
        if distro == "Debian":
            return ("172.24.2.2",
                    '# >>> proxy-manager begin >>>\n'
                    'export HTTP_PROXY="http://172.24.0.1:7897"\n',
                    "http://172.24.0.1:7897")
        return None

    monkeypatch.setattr(pm, "_wsl_ip_and_block_one", fake_one)
    ip, block, git = pm.get_wsl_ip_and_block()
    assert ip == "172.24.1.1"
    assert "HTTP_PROXY" in block
    assert git == "http://172.24.0.1:7897"


def test_read_wsl_block_skips_empty_default_distro(monkeypatch):
    monkeypatch.setattr(pm, "_wsl_targets", lambda: ["Ubuntu", "Debian"])
    calls = []

    def fake_run(cmd, **kwargs):
        distro = ""
        if "-d" in cmd:
            distro = cmd[cmd.index("-d") + 1]
        calls.append(distro)
        out = ""
        if distro == "Debian":
            out = "# >>> proxy-manager begin >>>\nexport HTTP_PROXY=x\n"
        return SimpleNamespace(stdout=out, returncode=0)

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    text = pm._read_wsl_block()
    assert "HTTP_PROXY=x" in text
    assert calls == ["Ubuntu", "Debian"]
