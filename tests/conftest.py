"""Put the project root on sys.path so tests can import app / proxy_manager."""

import os
import sys
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config_store  # noqa: E402  (needs ROOT on sys.path first)
import proxy_manager as pm  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Redirect every config_store path into the per-test temp dir.

    Without this, save_config's os.makedirs(APP_DIR), the cross-process
    config.json.lock and any test that forgets to patch CONFIG_FILE all
    reach the real %APPDATA%\\ProxyManager — writing the developer's live
    config from a test run. config_store resolves these names from its own
    module globals at call time (js_api / app.py / tray_icon import the
    functions, not the paths), so patching config_store is enough.
    """
    cfg_dir = tmp_path / "pm_config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_store, "APP_DIR", str(cfg_dir))
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg_dir / "config.json"))
    # Sticky per-process warning: reset so one test's corrupt-config recovery
    # cannot leak into another test's config_load assertion.
    monkeypatch.setattr(config_store, "_load_warning", None)


@pytest.fixture(autouse=True)
def _reset_wsl_available_cache(monkeypatch):
    """Clear the wsl_available TTL cache around every test.

    A hot positive cached by one test would otherwise leak into the next
    (and into alphabetically later files), masking a patched subprocess.run
    that expects a fresh probe — the same cross-file leak the distro cache
    guards against in tests/test_wsl_distro_cache.py.
    """
    monkeypatch.setattr(pm, "_wsl_available_cache", None)
    yield


@pytest.fixture(autouse=True, scope="session")
def no_real_proxy_probe():
    """Kill the background speed loop's TCP probe for the whole session.

    Api.__init__ starts _speed_loop (js_api.py:1327), which calls
    pm.proxy_port_alive(host, port) → socket.create_connection once a second.
    Every test that builds an Api therefore probes 127.0.0.1:7897 for real,
    and the daemon thread keeps firing after the test that built it has
    finished — so patching pm.proxy_port_alive per test is not enough (the
    thread outlives the patch). Guarding the socket layer session-wide is:
    OSError is what a refused connection raises, so proxy_port_alive simply
    reports "proxy down", exactly as before.
    """
    with mock.patch.object(pm.socket, "create_connection",
                           side_effect=OSError("tests must not open real sockets")):
        yield
