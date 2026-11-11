"""_wsl_distros TTL cache.

One port switch walks the distro list ~10 times (one per WSL status read and
per WSL-side setter target), each a separate cold `wsl -l -q`, so the
enumeration is cached for a couple of seconds. A failure, a timeout or an
empty result must NOT be cached: WSL coming back a second later has to be
visible again instead of being frozen as "unusable".

The autouse fixture clears the cache around every test: without it, the last
test's hot cache leaks into the next one (and into alphabetically later
files such as tests/test_wsl_status.py, which patches subprocess.run and
expects a fresh enumeration).
"""

import subprocess
import time
from types import SimpleNamespace

import pytest

import proxy_manager as pm


@pytest.fixture(autouse=True)
def _reset_distro_cache(monkeypatch):
    monkeypatch.setattr(pm, "_wsl_distros_cache", None)
    yield


def _payload(names):
    return ("\r\n".join(names) + "\r\n").encode("utf-16-le")


def _counting_run(result):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return result
    fake_run.calls = calls
    return fake_run


def test_wsl_distros_cached_within_ttl(monkeypatch):
    """一次换端口里的多次枚举只发起一次子进程；返回值相等但是不同对象
    （调用方不得改到缓存）。"""
    fake = _counting_run(SimpleNamespace(returncode=0,
                                         stdout=_payload(["Ubuntu", "Debian"])))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    first = pm._wsl_distros()
    second = pm._wsl_distros()

    assert fake.calls == [["wsl", "-l", "-q"]]
    assert first == ["Ubuntu", "Debian"] == second
    assert first is not second


def test_wsl_distros_cache_expires(monkeypatch):
    """超过 TTL 后重新发起子进程（用户新装的发行版不会长时间不可见）。"""
    monkeypatch.setattr(pm, "_wsl_distros_cache",
                        (time.monotonic() - 10, ["Ubuntu"]))
    fake = _counting_run(SimpleNamespace(returncode=0,
                                         stdout=_payload(["Debian"])))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm._wsl_distros() == ["Debian"]
    assert len(fake.calls) == 1


def test_wsl_distros_failure_not_cached(monkeypatch):
    """returncode != 0：返回空列表且不写缓存——WSL 暂时不可用不能被冻结。"""
    fake = _counting_run(SimpleNamespace(returncode=1, stdout=b""))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm._wsl_distros() == []
    assert pm._wsl_distros_cache is None
    assert pm._wsl_distros() == []
    assert len(fake.calls) == 2


def test_wsl_distros_timeout_not_cached(monkeypatch):
    """超时同理：下次调用必须重试。"""

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 15)

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    assert pm._wsl_distros() == []
    assert pm._wsl_distros_cache is None
    assert pm._wsl_distros() == []
    assert pm._wsl_distros_cache is None


def test_empty_distro_list_not_cached(monkeypatch):
    """枚举结果为空（WSL 装好但没有用户发行版）同样不写缓存。"""
    fake = _counting_run(SimpleNamespace(returncode=0, stdout=b""))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm._wsl_distros() == []
    assert pm._wsl_distros_cache is None
    assert pm._wsl_distros() == []
    assert len(fake.calls) == 2


def test_invalidate_wsl_distros_cache_forces_refetch(monkeypatch):
    """restart_wsl 之后调用 invalidate：缓存作废，下一次读取重新枚举。"""
    fake = _counting_run(SimpleNamespace(returncode=0,
                                         stdout=_payload(["Ubuntu"])))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm._wsl_distros() == ["Ubuntu"]
    assert pm._wsl_distros_cache is not None
    pm.invalidate_wsl_distros_cache()
    assert pm._wsl_distros_cache is None
    assert pm._wsl_distros() == ["Ubuntu"]
    assert len(fake.calls) == 2


def test_wsl_distros_cache_returns_copy(monkeypatch):
    """命中缓存后改写返回值不得污染模块级缓存。"""
    fake = _counting_run(SimpleNamespace(returncode=0,
                                         stdout=_payload(["Ubuntu", "Debian"])))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    cold = pm._wsl_distros()
    hot = pm._wsl_distros()
    hot.append("Evil")

    assert cold == ["Ubuntu", "Debian"]
    assert pm._wsl_distros() == ["Ubuntu", "Debian"]
    assert pm._wsl_distros_cache[1] == ["Ubuntu", "Debian"]
    assert len(fake.calls) == 1
