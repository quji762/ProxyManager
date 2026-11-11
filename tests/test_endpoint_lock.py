"""Endpoint mutations (UI toggles / port switch) and the tray-quit
clear_all_endpoints must serialize on one instance-level lock: _restore,
applied_* and _git_managed are only consistent when mutated inside one
critical section. Everything here runs against fakes — no real endpoints."""

import threading
from unittest.mock import patch

import config_store
import proxy_manager as pm
from js_api import Api


def _make_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "c.json"))
    with patch("proxy_manager.get_net_counters", return_value=None):
        return Api()


def test_clear_all_endpoints_holds_mutation_lock(tmp_path, monkeypatch):
    api = _make_api(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def slow_set(*a, **k):
        started.set()
        release.wait(5)
        return True

    with patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.set_ps_proxy", side_effect=slow_set), \
         patch("js_api.pm.set_sys_proxy", return_value=True), \
         patch("js_api.pm.set_git_proxy", return_value=True), \
         patch("js_api.pm.set_wsl_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy", return_value=True), \
         patch("js_api.pm.set_pip_proxy", return_value=True), \
         patch("js_api.pm.set_npm_proxy_wsl", return_value=True), \
         patch("js_api.pm.set_pip_proxy_wsl", return_value=True), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}):
        worker = threading.Thread(target=api.clear_all_endpoints, daemon=True)
        worker.start()
        assert started.wait(5)      # quit-clear is mid-flight on "ps"

        # The lock is held for the WHOLE sequence, not per toggle.
        assert not api._endpoint_mutation_lock.acquire(blocking=False)

        # A toggle issued meanwhile must wait, not interleave.
        done = []
        t = threading.Thread(
            target=lambda: done.append(api.do_toggle("npm", False)),
            daemon=True)
        t.start()
        t.join(0.3)
        assert done == []

        release.set()
        worker.join(5)
        t.join(5)

    assert done and done[0].get("ok") is True
    assert api._endpoint_mutation_lock.acquire(blocking=False)  # released
    api._endpoint_mutation_lock.release()


def test_switch_port_runs_inside_mutation_lock(tmp_path, monkeypatch):
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.scheme, api.user, api.password = "http", "", ""
    result = {}
    started = threading.Event()

    def run():
        started.set()
        result.update(api.switch_port("7890"))

    with patch("js_api.pm.get_ps_status", return_value=pm.UNSET), \
         patch("js_api.pm.get_sys_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_git_proxy_win", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy", return_value=pm.UNSET), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_wsl_network_mode", return_value="Mirrored"), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}):
        api._endpoint_mutation_lock.acquire()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert started.wait(5)
        t.join(0.5)
        assert result == {}     # must not migrate while the lock is held
        api._endpoint_mutation_lock.release()
        t.join(5)

    assert result.get("ok") is True
    assert result.get("applied") == []


def test_restart_wsl_does_not_hold_mutation_lock_during_subprocess(
        tmp_path, monkeypatch):
    """restart_wsl can block for tens of seconds — the slow pm.restart_wsl
    subprocess must run OUTSIDE _endpoint_mutation_lock (a tray-quit clear or
    a toggle issued meanwhile must proceed), while the shared-state reads and
    writes around it stay inside. All wsl.exe-facing reads are mocked so no
    real subprocess runs during the test."""
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    in_restart = threading.Event()
    release_restart = threading.Event()

    def slow_restart():
        in_restart.set()
        release_restart.wait(5)
        return True

    lock_free_during_restart = []

    with patch("js_api.pm.restart_wsl", side_effect=slow_restart), \
         patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
         patch("js_api.pm.get_wsl_gateway_ip", return_value="172.24.0.1"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": False, "git": pm.UNSET}):
        worker = threading.Thread(target=api.restart_wsl, daemon=True)
        worker.start()
        assert in_restart.wait(5)

        # The lock must be FREE while the WSL restart subprocess runs.
        lock_free_during_restart.append(
            api._endpoint_mutation_lock.acquire(blocking=False))
        if lock_free_during_restart[0]:
            api._endpoint_mutation_lock.release()

        # A toggle issued during the restart must not be blocked either.
        with patch("js_api.pm.set_npm_proxy", return_value=True), \
             patch("js_api.pm.get_npm_proxy", return_value=pm.UNSET):
            done = []
            t = threading.Thread(
                target=lambda: done.append(api.do_toggle("npm", False)),
                daemon=True)
            t.start()
            t.join(1.0)
            assert done, "do_toggle blocked while restart_wsl ran"

        release_restart.set()
        worker.join(5)

    assert not worker.is_alive()
    assert lock_free_during_restart == [True]
    assert api._endpoint_mutation_lock.acquire(blocking=False)  # released
    api._endpoint_mutation_lock.release()


def test_restart_wsl_skips_foreign_git_npm_pip(tmp_path, monkeypatch):
    """NAT loopback rewrite after wsl --shutdown must not clobber a
    user-configured git/npm/pip proxy that this tool did not write."""
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    calls = []

    def rec(name):
        def fn(*a, **k):
            calls.append(name)
            return True
        return fn

    foreign = "http://company-proxy:8080"
    with patch("js_api.pm.restart_wsl", return_value=True), \
         patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
         patch("js_api.pm.get_wsl_gateway_ip", return_value="172.25.0.1"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=foreign), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=foreign), \
         patch.object(api, "refresh_item",
                      return_value={"on": True, "git": foreign}), \
         patch("js_api.pm.set_wsl_proxy", side_effect=rec("wsl")), \
         patch("js_api.pm.set_git_proxy", side_effect=rec("git")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=rec("npm")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=rec("pip")):
        assert api.restart_wsl()["ok"] is True

    assert "wsl" in calls
    assert "git" not in calls
    assert "npm" not in calls
    assert "pip" not in calls


def test_restart_wsl_rewrites_stale_gateway(tmp_path, monkeypatch):
    """A URL this tool wrote with the previous NAT gateway must be updated
    after the subnet moves. The rewrite is gated on the persisted
    _wsl_written record: the stale file URL equals what we last wrote, the
    current gateway does not matter."""
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.port = "127.0.0.1", "7897"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    stale = "http://172.24.0.1:7897"
    api._wsl_written = {"gitwsl": stale, "npmwsl": stale, "pipwsl": stale}
    calls = []

    def rec(name):
        def fn(*a, **k):
            calls.append(name)
            return True
        return fn

    with patch("js_api.pm.restart_wsl", return_value=True), \
         patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
         patch("js_api.pm.get_wsl_gateway_ip", return_value="172.25.0.1"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=stale), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=stale), \
         patch.object(api, "refresh_item",
                      return_value={"on": True, "git": stale}), \
         patch("js_api.pm.set_wsl_proxy", side_effect=rec("wsl")), \
         patch("js_api.pm.set_git_proxy", side_effect=rec("git")), \
         patch("js_api.pm.set_npm_proxy_wsl", side_effect=rec("npm")), \
         patch("js_api.pm.set_pip_proxy_wsl", side_effect=rec("pip")):
        assert api.restart_wsl()["ok"] is True

    assert calls == ["wsl", "git", "npm", "pip"]
    # The rewritten (new-gateway) URLs replace the stale records.
    fresh = "http://172.25.0.1:7897"
    assert api._wsl_written == {"gitwsl": fresh, "npmwsl": fresh,
                                "pipwsl": fresh}


def test_restart_wsl_uses_applied_address_not_form(tmp_path, monkeypatch):
    """Form fields may have been edited but not applied; NAT rewrite after
    wsl --shutdown must retarget the applied loopback, not the form."""
    api = _make_api(tmp_path, monkeypatch)
    api.host, api.port = "10.0.0.1", "1080"
    api.applied_host, api.applied_port = "127.0.0.1", "7897"
    api.applied_kw = {"scheme": "http", "user": "", "password": ""}
    written = []

    def capture(h, p, on=True, **k):
        written.append((h, str(p)))
        return True

    with patch("js_api.pm.restart_wsl", return_value=True), \
         patch("js_api.pm.get_wsl_network_mode", return_value="NAT"), \
         patch("js_api.pm.get_wsl_gateway_ip", return_value="172.25.0.1"), \
         patch("js_api.pm.get_npm_proxy_wsl", return_value=pm.UNSET), \
         patch("js_api.pm.get_pip_proxy_wsl", return_value=pm.UNSET), \
         patch.object(api, "refresh_item",
                      return_value={"on": True, "git": pm.UNSET}), \
         patch("js_api.pm.set_wsl_proxy", side_effect=capture):
        assert api.restart_wsl()["ok"] is True

    assert written == [("172.25.0.1", "7897")]
