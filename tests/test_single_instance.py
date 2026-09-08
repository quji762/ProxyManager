"""Single-instance guard: when the mutex cannot even be created the app must
CONTINUE (no handle), not quit as if another instance were running."""

import app as appmod


class _FailingKernel32:
    """Stand-in for kernel32 whose CreateMutexW fails (returns a NULL handle).

    Carries the same attribute set _wake_or_exit touches — including
    CloseHandle's argtypes/restype assignment — so the function under test
    exercises the full setup path."""


def _make_failing_kernel32(*a, **k):
    fk = _FailingKernel32()

    def create_mutex(*_a):
        return None

    fk.CreateMutexW = create_mutex
    fk.CloseHandle = lambda h: 1
    return fk


def test_wake_or_exit_survives_mutex_create_failure(monkeypatch):
    monkeypatch.delenv("PM_NO_SINGLETON", raising=False)
    monkeypatch.setattr(appmod.ctypes, "WinDLL", _make_failing_kernel32)
    monkeypatch.setattr(appmod.ctypes, "get_last_error", lambda: 5)
    # Signal-file removal is best-effort on this path (missing file is fine).
    monkeypatch.setattr(appmod.os, "remove", lambda p: None)
    # A non-None sentinel (0) keeps main() running; None meant "already
    # running" and used to make the app exit without a window.
    assert appmod._wake_or_exit() == 0
