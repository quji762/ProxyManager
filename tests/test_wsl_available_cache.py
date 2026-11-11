"""wsl_available TTL cache.

A batch "enable all" runs the WSL pre-flight once per WSL-side endpoint
(wsl / gitwsl / npmwsl / pipwsl), each a separate cold `wsl -e echo ok`, so a
positive result is cached for a few seconds to collapse the burst into one
probe. A failure or timeout must NOT be cached: WSL coming back a second
later has to be visible again instead of being frozen as "unusable", and the
batch aborts on the first unavailable endpoint anyway.

restart_wsl --shutdown makes availability transiently false, so it drops the
cached positive; the wiring is asserted here too.

The autouse fixture clears the cache around every test so a hot cache cannot
leak into the next one (mirrors tests/test_wsl_distro_cache.py).
"""

import subprocess
import time
from types import SimpleNamespace

import pytest

import proxy_manager as pm


@pytest.fixture(autouse=True)
def _reset_available_cache(monkeypatch):
    monkeypatch.setattr(pm, "_wsl_available_cache", None)
    yield


def _counting_run(result):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return result
    fake_run.calls = calls
    return fake_run


def test_wsl_available_cached_within_ttl(monkeypatch):
    """一次批量启用里的多次探活只发起一次子进程。"""
    fake = _counting_run(SimpleNamespace(returncode=0, stdout=b"ok\n"))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm.wsl_available() is True
    assert pm.wsl_available() is True
    assert fake.calls == [["wsl", "-e", "echo", "ok"]]


def test_wsl_available_cache_expires(monkeypatch):
    """超过 TTL 后重新探活（WSL 状态变化不会长时间不可见）。"""
    monkeypatch.setattr(pm, "_wsl_available_cache",
                        (time.monotonic() - 10, True))
    fake = _counting_run(SimpleNamespace(returncode=0, stdout=b"ok\n"))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm.wsl_available() is True
    assert len(fake.calls) == 1


def test_wsl_available_failure_not_cached(monkeypatch):
    """returncode != 0：返回 False 且不写缓存——WSL 暂时不可用不能被冻结。"""
    fake = _counting_run(SimpleNamespace(returncode=1, stdout=b""))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm.wsl_available() is False
    assert pm._wsl_available_cache is None
    assert pm.wsl_available() is False
    assert len(fake.calls) == 2


def test_wsl_available_timeout_not_cached(monkeypatch):
    """超时同理：下次调用必须重试。"""
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 15)

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    assert pm.wsl_available() is False
    assert pm._wsl_available_cache is None
    assert pm.wsl_available() is False
    assert pm._wsl_available_cache is None


def test_invalidate_wsl_available_cache_forces_refetch(monkeypatch):
    """invalidate 之后缓存作废，下一次读取重新探活。"""
    fake = _counting_run(SimpleNamespace(returncode=0, stdout=b"ok\n"))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm.wsl_available() is True
    assert pm._wsl_available_cache is not None
    pm.invalidate_wsl_available_cache()
    assert pm._wsl_available_cache is None
    assert pm.wsl_available() is True
    assert len(fake.calls) == 2


def test_restart_wsl_invalidates_available_cache(monkeypatch):
    """restart_wsl --shutdown 后必须作废可用性缓存：预先塞入热缓存，重启
    成功后缓存应为空，且下一次 wsl_available() 重新发起子进程探活。"""
    monkeypatch.setattr(pm, "_wsl_available_cache",
                        (time.monotonic(), True))
    fake = _counting_run(SimpleNamespace(returncode=0, stdout=b"ok\n"))
    monkeypatch.setattr(pm.subprocess, "run", fake)

    assert pm.restart_wsl() is True
    assert pm._wsl_available_cache is None
    # 重启自身的探活不计入 wsl_available；重启后再读必须重新探活。
    before = len(fake.calls)
    assert pm.wsl_available() is True
    assert len(fake.calls) == before + 1