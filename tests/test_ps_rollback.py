"""PowerShell enable mid-write rollback (R4 failure-path coverage).

set_ps_proxy snapshots all eight env names before writing, and if a write
fails partway it must put every already-written name back exactly (original
value+type, or deleted when it did not exist). Existing tests mock the whole
setter, so this rollback loop had no coverage. A blind delete on failure
would drop a proxy the user configured by hand — a core-invariant violation.

The fake winreg models the real per-name store (missing key == does not
exist) and the (value, type) pair QueryValueEx/SetValueEx exchange.
"""

from unittest.mock import patch

import proxy_manager as pm


class FakeKey(dict):
    """name -> (value, reg_type). Missing key means the value does not exist."""


class FakeWinreg:
    HKEY_CURRENT_USER = 1
    KEY_SET_VALUE = 0x0002
    KEY_READ = 0x0001
    REG_SZ = 1
    REG_DWORD = 4

    def __init__(self, store, fail_name=None, fail_value=None):
        self.store = store
        self.fail_name = fail_name
        self.fail_value = fail_value

    def OpenKey(self, *_a, **_k):
        return self.store

    def CloseKey(self, *_a, **_k):
        pass

    def QueryValueEx(self, key, name):
        if name not in key:
            raise FileNotFoundError(name)
        return key[name]

    def SetValueEx(self, key, name, _reserved, reg_type, value):
        if name == self.fail_name and (
                self.fail_value is None or value == self.fail_value):
            raise OSError(f"simulated write failure on {name}")
        key[name] = (value, reg_type)

    def DeleteValue(self, key, name):
        if name not in key:
            raise FileNotFoundError(name)
        del key[name]


OUR_URL = "http://127.0.0.1:7897"


def test_ps_enable_midwrite_rollback_restores_originals():
    # User had HTTP_PROXY set, HTTPS_PROXY absent, ALL_PROXY set.
    store = FakeKey({
        "HTTP_PROXY": ("http://user-original:1111", FakeWinreg.REG_SZ),
        "ALL_PROXY": ("http://user-all:2222", FakeWinreg.REG_SZ),
    })
    # Fail on the 3rd URL write (ALL_PROXY): HTTP_PROXY/HTTPS_PROXY landed.
    fake = FakeWinreg(store, fail_name="ALL_PROXY", fail_value=OUR_URL)
    with patch.object(pm, "winreg", fake), \
            patch.object(pm, "_broadcast_env_change"):
        ret = pm.set_ps_proxy("127.0.0.1", "7897", True)

    assert ret is not True            # failure reported, not silent success
    assert "ALL_PROXY" in str(ret)
    # Already-written names rolled back to the exact pre-enable state.
    assert store["HTTP_PROXY"] == ("http://user-original:1111",
                                   FakeWinreg.REG_SZ)
    assert "HTTPS_PROXY" not in store   # was absent → deleted again
    # The name whose write failed is untouched (never appended to `written`).
    assert store["ALL_PROXY"] == ("http://user-all:2222", FakeWinreg.REG_SZ)


def test_ps_enable_midwrite_rollback_deletes_new_keys():
    # No proxy vars existed before: a partial enable must leave none behind.
    store = FakeKey()
    fake = FakeWinreg(store, fail_name="https_proxy", fail_value=OUR_URL)
    with patch.object(pm, "winreg", fake), \
            patch.object(pm, "_broadcast_env_change"):
        ret = pm.set_ps_proxy("127.0.0.1", "7897", True)

    assert ret is not True
    # Every name written before the failure was new → all deleted again.
    for n in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy"):
        assert n not in store
    assert "https_proxy" not in store


def test_ps_enable_success_writes_both_spellings():
    store = FakeKey()
    fake = FakeWinreg(store)
    with patch.object(pm, "winreg", fake), \
            patch.object(pm, "_broadcast_env_change"):
        ret = pm.set_ps_proxy("127.0.0.1", "7897", True)

    assert ret is True
    for n in pm.PS_ENV_URL_NAMES:
        assert store[n] == (OUR_URL, FakeWinreg.REG_SZ)
    # NO_PROXY merged in both spellings.
    for n in pm.PS_ENV_NO_PROXY_NAMES:
        assert n in store