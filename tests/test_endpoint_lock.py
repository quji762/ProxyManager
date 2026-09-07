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
