"""Tray promotion matching: only entries pinned to THIS tool may get their
IsPromoted flag rewritten. All winreg access below is faked — nothing here
touches the real registry.

The second half covers the tray's all-on/all-off actions and the enabled-
endpoint count: both must stay off the pystray message loop and must never
read the form address or shell out to wsl.exe/registry."""

import json
import sys
import threading
import time
import types

import PIL.Image
import config_store
import proxy_manager as pm
import tray_icon
from tray_icon import _tray_executable_matches


def test_unrelated_python_entries_never_match(monkeypatch):
    """The old interpreter-family match rewrote IsPromoted for every other
    Python tray program on the system; only a full-path match counts now."""
    monkeypatch.setattr(sys, "executable", r"C:\Py313\pythonw.exe")
    assert not _tray_executable_matches(r"C:\Other\pythonw.exe")
    assert not _tray_executable_matches(r"C:\Other\python.exe")
    assert not _tray_executable_matches(r"C:\Other\pythonw3.13.exe")
    assert not _tray_executable_matches("")


def test_own_executable_path_matches(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Py313\pythonw.exe")
    assert _tray_executable_matches(r"C:\Py313\pythonw.exe")
    assert _tray_executable_matches("C:/Py313/PYTHONW.exe")  # slashes + case
    assert not _tray_executable_matches(r"C:\Py313\python.exe")


class _FakeNotifyIconReg:
    """In-memory HKCU\\Control Panel\\NotifyIconSettings stand-in."""

    def __init__(self, entries):
        # subkey name -> {value name: (data, type)}
        self.entries = entries
        self.writes = []

    def install(self, monkeypatch):
        import winreg
        base = r"Control Panel\NotifyIconSettings"
        reg = self
        names = list(self.entries)

        def open_key(root, path, reserved=0, access=None):
            if path == base:
                return ("base",)
            sub = path.rsplit("\\", 1)[-1]
            if sub not in reg.entries:
                raise OSError(sub)
            return ("sub", sub)

        def enum_key(key, index):
            if index >= len(names):
                raise OSError("no more subkeys")
            return names[index]

        def query(key, name):
            try:
                return reg.entries[key[1]][name]
            except KeyError:
                raise FileNotFoundError(name)

        def set_value(key, name, reserved, vtype, value):
            reg.writes.append((key[1], name, value))

        monkeypatch.setattr(winreg, "OpenKey", open_key)
        monkeypatch.setattr(winreg, "EnumKey", enum_key)
        monkeypatch.setattr(winreg, "QueryValueEx", query)
        monkeypatch.setattr(winreg, "SetValueEx", set_value)
        monkeypatch.setattr(winreg, "CloseKey", lambda key: None)


def test_promote_writes_only_own_entries(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Py313\pythonw.exe")
    fake = _FakeNotifyIconReg({
        # exact tooltip match: ours even with a foreign path
        "tip": {"Tooltip": (pm.APP_TITLE, 1),
                "ExecutablePath": (r"C:\Somewhere\else.exe", 1)},
        # empty tooltip but our own image: matched by ExecutablePath
        "ours": {"Tooltip": ("", 1),
                 "ExecutablePath": (r"C:\Py313\pythonw.exe", 1)},
        # tooltip merely CONTAINS the title + a family-matching interpreter:
        # the old matching rewrote this one, the new one must not
        "foreign": {"Tooltip": ("别的代理管理器工具", 1),
                    "ExecutablePath": (r"C:\Other\pythonw.exe", 1)},
    })
    fake.install(monkeypatch)

    assert tray_icon._promote_tray_icon(retries=1) is True
    assert set(fake.writes) == {("tip", "IsPromoted", 1),
                                ("ours", "IsPromoted", 1)}


def test_promote_matches_english_tooltip(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Py313\pythonw.exe")
    fake = _FakeNotifyIconReg({
        "en": {"Tooltip": ("Proxy Manager", 1),
               "ExecutablePath": (r"C:\Somewhere\else.exe", 1)},
        "foreign": {"Tooltip": ("Other Manager", 1),
                    "ExecutablePath": (r"C:\Other\pythonw.exe", 1)},
    })
    fake.install(monkeypatch)
    assert tray_icon._promote_tray_icon(retries=1) is True
    assert fake.writes == [("en", "IsPromoted", 1)]


# ── tray all-on / all-off + enabled-endpoint count ───────────────────────────

def _write_config(data):
    """Write a config the tray helpers will load (the session fixture already
    redirected config_store.CONFIG_FILE into the per-test temp dir)."""
    with open(config_store.CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class _FakeCountApi:
    """Api stand-in for _tray_enabled_count.

    Reading the FORM address is a contract violation ("只认已应用口径"), so
    host/port raise instead of quietly returning a value. _ours_proxy_urls
    records which endpoints it was asked about: a WSL target would resolve
    the NAT gateway with a wsl.exe subprocess and freeze the menu.
    """

    def __init__(self, wsl_written=None, applied_host="127.0.0.1"):
        self.applied_host = applied_host
        self.applied_port = "7897"
        self._wsl_written = wsl_written if wsl_written is not None else {
            "gitwsl": "http://127.0.0.1:7897",
            "npmwsl": "http://127.0.0.1:7897",
            "pipwsl": None,
        }
        self.ours_calls = []

    @property
    def host(self):
        raise AssertionError("tray count must not read the form host")

    @property
    def port(self):
        raise AssertionError("tray count must not read the form port")

    def _ours_proxy_urls(self, target=None):
        self.ours_calls.append(target)
        return {"http://127.0.0.1:7897"}


class _FakeToggleApi:
    """Api stand-in for the tray all-on/all-off workers: records every
    prepare_toggle / do_toggle / clear_all_endpoints call instead of touching
    the system."""

    def __init__(self, precheck=None, default_precheck=None, results=None):
        self.host = "form.invalid"        # only the no-applied-record fallback
        self.port = "1"
        self.applied_host = "127.0.0.1"
        self.applied_port = "7897"
        self.applied_kw = {"scheme": "http", "user": "", "password": ""}
        self._wsl_written = {}
        self._icon = None
        self.precheck = precheck or {}
        self.default_precheck = default_precheck or {"ok": True}
        self.results = results or {}
        self.prepared = []
        self.toggled = []
        self.cleared = 0
        self.entered = threading.Event()
        self.gate = None                  # optional Event do_toggle blocks on

    def prepare_toggle(self, target, on, host=None, port=None):
        self.prepared.append((target, host, port))
        return self.precheck.get(target, self.default_precheck)

    def do_toggle(self, target, on, host=None, port=None, **kw):
        self.toggled.append((target, host, port, kw))
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        return self.results.get(target, {"ok": True})

    def clear_all_endpoints(self):
        self.cleared += 1
        return True


class _FakePystray:
    """In-memory pystray stand-in: Menu/MenuItem/Icon only record what
    _make_tray builds, and Icon.run fires setup synchronously so the 5-second
    readiness wait returns at once."""

    def __init__(self):
        self.menus = []
        self.icons = []

    def install(self, monkeypatch):
        fake = self

        class _Image:
            def convert(self, mode):
                return self

        class MenuItem:
            def __init__(self, text, action=None, default=False):
                self.text, self.action, self.default = text, action, default

        class Menu:
            def __init__(self, *items):
                self.items = list(items)
                fake.menus.append(self)

        class Icon:
            def __init__(self, name, image, title, menu):
                self.name, self.image = name, image
                self.title, self.menu = title, menu
                self.visible = False
                self.notifications = []
                self.menu_updates = 0
                fake.icons.append(self)

            def run(self, setup=None, **kwargs):
                if setup is not None:
                    setup(self)

            def stop(self):
                pass

            def notify(self, message, title=None):
                self.notifications.append((message, title))

            def update_menu(self):
                self.menu_updates += 1

        monkeypatch.setitem(sys.modules, "pystray", types.SimpleNamespace(
            Menu=Menu, MenuItem=MenuItem, Icon=Icon))
        monkeypatch.setattr(PIL.Image, "open", lambda path: _Image())


def _build_tray(monkeypatch, api):
    """_make_tray with pystray/PIL faked; returns the fake Icon."""
    fake = _FakePystray()
    fake.install(monkeypatch)
    monkeypatch.setattr(tray_icon, "_tray_busy", False)
    icon = tray_icon._make_tray(api, None)
    return icon


def _wait_tray_idle(timeout=10.0):
    """Wait until the tray worker finished (it clears _tray_busy last)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not tray_icon._tray_busy:
            return True
        time.sleep(0.01)
    return False


def test_tray_labels_have_all_toggle_and_count():
    for lang in ("zh", "en"):
        labels = tray_icon._tray_labels(lang)
        for key in ("allOn", "allOff", "countFmt", "busyAllOn", "busyAllOff"):
            assert labels[key], (lang, key)
        assert labels["countFmt"].format(n=2)


def test_tray_count_reads_applied_not_form(monkeypatch):
    """The count is built from the applied record + persisted switches, never
    from the form fields (a typed-but-never-applied address is not on
    anywhere), and it must never probe a WSL endpoint (that path shells out to
    wsl.exe and would stall the menu rebuild)."""
    _write_config({"powershell": True, "npm": True, "pip": False,
                   "git_wsl": True, "npmwsl": True, "pipwsl": False,
                   "wsl": True})
    api = _FakeCountApi()
    assert tray_icon._tray_enabled_count(api) == (4, 9)
    # ps + npm + the two WSL endpoints with a written-URL record (gitwsl,
    # npmwsl). "wsl" has a switch but no written-URL record, "sys" has no
    # persisted switch at all: neither is counted rather than guessed.
    assert set(api.ours_calls) == {"ps", "npm"}
    assert not (set(api.ours_calls) & set(tray_icon._TRAY_WSL_TARGETS))


def test_tray_count_zero_without_applied_record(monkeypatch):
    """No applied record means nothing is provably ours — even with every
    switch on, the count stays 0 (and nothing is probed)."""
    _write_config({"powershell": True, "npm": True, "pip": True,
                   "git_wsl": True, "npmwsl": True, "pipwsl": True,
                   "wsl": True})
    api = _FakeCountApi(applied_host="")
    assert tray_icon._tray_enabled_count(api) == (0, 9)
    assert api.ours_calls == []


def test_tray_all_toggle_targets_match_clear_all_endpoints():
    targets = tray_icon._tray_all_toggle_targets()
    assert targets == ("ps", "sys", "wsl", "gitwin", "gitwsl",
                       "npm", "pip", "npmwsl", "pipwsl")
    assert len(targets) == 9


def test_menu_contains_count_and_all_toggle_items(monkeypatch):
    _write_config({"powershell": True, "npm": True, "pip": False,
                   "git_wsl": True, "npmwsl": True, "pipwsl": False,
                   "wsl": True})
    icon = _build_tray(monkeypatch, _FakeCountApi())
    items = icon.menu.items
    assert len(items) == 5
    # count / all on / all off / show / quit
    assert items[0].action is None          # read-only → disabled item
    assert callable(items[1].action)
    assert callable(items[2].action)
    assert callable(items[3].action) and items[3].default is True
    assert callable(items[4].action)
    assert items[0].text() == "代理已启用 4 个端点"
    assert items[1].text() == "全部启用"
    assert items[2].text() == "全部关闭"
    assert items[3].text() == "显示主窗口"
    assert items[4].text() == "退出"


def test_all_on_runs_off_the_pystray_thread(monkeypatch):
    """The 9-endpoint enable sequence must not run on pystray's message-loop
    thread (each WSL target costs a wsl.exe probe plus seconds of writes), so
    on_all returns while do_toggle is still blocked and the worker finishes
    the job in the background."""
    api = _FakeToggleApi()
    api.gate = threading.Event()
    icon = _build_tray(monkeypatch, api)
    started = time.monotonic()
    icon.menu.items[1].action(icon, None)          # on_all
    elapsed = time.monotonic() - started
    assert api.entered.wait(5)                     # worker reached do_toggle
    assert elapsed < 1.0                           # the click itself returned
    api.gate.set()
    assert _wait_tray_idle()
    assert [t for t, *_ in api.toggled] == list(
        tray_icon._tray_all_toggle_targets())
    # feedback: a balloon before the work, a count balloon after, menu refreshed
    assert icon.notifications[0][0] == \
        tray_icon._tray_labels("zh")["busyAllOn"]
    assert "9" in icon.notifications[-1][0]
    assert icon.menu_updates >= 1


def test_all_on_skips_endpoints_that_fail_precheck(monkeypatch):
    """{confirm} / {error} prechecks cannot open a modal dialog from the tray,
    so those endpoints are skipped and named in the report instead of being
    enabled blind."""
    api = _FakeToggleApi(
        precheck={"ps": {"ok": True},
                  "wsl": {"confirm": "shell", "shell": "tcsh"}},
        default_precheck={"confirm": "port"})
    icon = _build_tray(monkeypatch, api)
    icon.menu.items[1].action(icon, None)          # on_all
    assert _wait_tray_idle()
    assert [t for t, *_ in api.toggled] == ["ps"]
    assert [t for t, *_ in api.prepared] == list(
        tray_icon._tray_all_toggle_targets())
    report = icon.notifications[-1][0]
    assert "wsl" in report                          # the skipped endpoint


def test_all_on_stops_after_first_toggle_failure(monkeypatch):
    api = _FakeToggleApi(results={"sys": {"error": "写入失败"}})
    icon = _build_tray(monkeypatch, api)
    icon.menu.items[1].action(icon, None)          # on_all
    assert _wait_tray_idle()
    assert [t for t, *_ in api.toggled] == ["ps", "sys"]
    assert "wsl" not in [t for t, *_ in api.toggled]
    assert "sys" in icon.notifications[-1][0]       # failure reported


def test_all_off_uses_clear_all_endpoints(monkeypatch):
    api = _FakeToggleApi()
    icon = _build_tray(monkeypatch, api)
    icon.menu.items[2].action(icon, None)          # off_all
    assert _wait_tray_idle()
    assert api.cleared == 1
    assert api.toggled == []                        # no enable path touched
    assert icon.notifications[0][0] == \
        tray_icon._tray_labels("zh")["busyAllOff"]
    assert icon.notifications[-1][0] == \
        tray_icon._tray_labels("zh")["allOff"]


def test_second_click_while_worker_runs_is_ignored(monkeypatch):
    """A busy worker must not be followed by a concurrent mutation."""
    api = _FakeToggleApi()
    api.gate = threading.Event()
    icon = _build_tray(monkeypatch, api)
    icon.menu.items[1].action(icon, None)          # on_all (blocks in worker)
    assert api.entered.wait(5)
    icon.menu.items[2].action(icon, None)          # off_all while busy
    icon.menu.items[1].action(icon, None)          # on_all again while busy
    assert api.cleared == 0
    assert len(api.toggled) == 1
    api.gate.set()
    assert _wait_tray_idle()
    assert api.cleared == 0
    assert len(api.toggled) == len(
        tray_icon._tray_all_toggle_targets())
