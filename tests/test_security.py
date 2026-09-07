"""安全加固回归测试：暂存 DPAPI 加密、环境变量键名白名单、git WSL unset 转义。"""

from __future__ import annotations

import unittest
from unittest import mock


class TestStashEncrypt(unittest.TestCase):
    """恢复暂存落盘前逐值 DPAPI 加密；读回时解密（旧版明文兼容）。"""

    def test_roundtrip_encrypts_url_and_env(self):
        import js_api

        restore = {
            "ps": {"env": {"HTTP_PROXY": "http://u:p@127.0.0.1:7897", "NO_PROXY": None}},
            "sys": {"url": "http://u:p@127.0.0.1:7897", "override": "localhost;<local>"},
        }
        fake = {"n": 0}

        def fake_encrypt(v):
            fake["n"] += 1
            return f"DPAPI:enc{fake['n']}"

        with mock.patch.object(js_api, "_dpapi_encrypt", fake_encrypt), \
                mock.patch.object(js_api, "_dpapi_decrypt",
                                  lambda v: v.replace("DPAPI:enc", "plain")):
            stored = js_api._stash_encrypt(restore)
            # URL 与 env 值被加密；override 非凭据保持明文
            self.assertTrue(stored["sys"]["url"].startswith("DPAPI:"))
            self.assertTrue(stored["ps"]["env"]["HTTP_PROXY"].startswith("DPAPI:"))
            self.assertIsNone(stored["ps"]["env"]["NO_PROXY"])
            self.assertEqual(stored["sys"]["override"], "localhost;<local>")
            back = js_api._stash_decrypt(stored)
            self.assertEqual(back["ps"]["env"]["HTTP_PROXY"], "plain1")
            self.assertEqual(back["sys"]["url"], "plain2")
            self.assertEqual(back["sys"]["override"], "localhost;<local>")

    def test_legacy_plaintext_stash_loads_unchanged(self):
        import js_api

        legacy = {"npm": {"url": "http://127.0.0.1:7890"}}
        back = js_api._stash_decrypt(legacy)
        self.assertEqual(back, legacy)


class TestPsEnvWhitelist(unittest.TestCase):
    """set_ps_env_vars 只接受四个代理变量名，拒绝任意 HKCU\\Environment 写入。"""

    def test_rejects_unlisted_key_names(self):
        import proxy_manager as pm

        written = {}

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_open = mock.MagicMock(return_value=FakeKey())
        fake_set = mock.MagicMock(side_effect=lambda k, n, *a: written.__setitem__(n, a[2]))
        with mock.patch.object(pm.winreg, "OpenKey", fake_open), \
                mock.patch.object(pm.winreg, "SetValueEx", fake_set), \
                mock.patch.object(pm.winreg, "DeleteValue", mock.MagicMock()), \
                mock.patch.object(pm.winreg, "CloseKey", mock.MagicMock()), \
                mock.patch.object(pm, "_broadcast_env_change", lambda: None):
            result = pm.set_ps_env_vars({
                "HTTP_PROXY": "http://127.0.0.1:7897",
                "EVIL_KEY": "arbitrary",
            })
        self.assertIs(result, True)
        self.assertIn("HTTP_PROXY", written)
        self.assertNotIn("EVIL_KEY", written)


class TestGitWslUnsetEscaping(unittest.TestCase):
    """git WSL unset 的 regex 回退分支：含单引号的 URL 不再逃逸 bash 单引号。"""

    def test_single_quote_in_url_cannot_break_quoting(self):
        import proxy_manager as pm

        cmd = pm._git_wsl_unset_cmd("http://a';reboot;'b:80")
        # ' 被转成 '\''（bash 单引号转义序列）；裸单引号不再出现
        self.assertIn("a'\\'';reboot", cmd)
        self.assertNotIn("a';reboot", cmd)


if __name__ == "__main__":
    unittest.main()
