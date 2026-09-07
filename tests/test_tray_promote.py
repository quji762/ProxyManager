"""Tray promotion matching: only entries pinned to THIS tool may get their
IsPromoted flag rewritten. All winreg access below is faked — nothing here
touches the real registry."""

import sys

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
