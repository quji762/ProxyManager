"""安全加固回归测试：暂存 DPAPI 加密、环境变量键名白名单、git WSL unset 转义。"""

from __future__ import annotations

import json
import threading
import time
import unittest
from unittest import mock

import config_store


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


class TestStringStashEncrypt(unittest.TestCase):
    """字符串形态暂存（npm/pip/gitwin/gitwsl 是裸 URL，可能带 user:pass@）
    落盘前也必须 DPAPI 加密；读回还原；已加密值幂等不双重加密。"""

    # 用真实 DPAPI（CryptProtectData 本机可用），保证"密文/明文"断言
    # 有实际区分度，而不是由 mock 决定形态。
    def _real(self):
        from config_store import _dpapi_decrypt, _dpapi_encrypt
        return _dpapi_encrypt, _dpapi_decrypt

    def test_string_url_encrypted_and_roundtrips(self):
        import js_api

        encrypt, decrypt = self._real()
        with mock.patch.object(js_api, "_dpapi_encrypt", encrypt), \
                mock.patch.object(js_api, "_dpapi_decrypt", decrypt):
            stored = js_api._stash_encrypt(
                {"npm": "http://user:secret@127.0.0.1:7897"})
            on_disk = stored["npm"]
            self.assertTrue(on_disk.startswith("DPAPI:"))
            self.assertNotIn("secret", on_disk)          # 明文凭据不落盘
            self.assertNotIn("user:", on_disk)
            back = js_api._stash_decrypt(stored)
            self.assertEqual(back["npm"], "http://user:secret@127.0.0.1:7897")

    def test_string_stash_idempotent_no_double_encryption(self):
        import js_api

        encrypt, decrypt = self._real()
        with mock.patch.object(js_api, "_dpapi_encrypt", encrypt), \
                mock.patch.object(js_api, "_dpapi_decrypt", decrypt):
            first = js_api._stash_encrypt({"gitwin": "http://u:p@h:1"})
            again = js_api._stash_encrypt(first)     # 再加密一遍必须原样
            self.assertEqual(again, first)
            self.assertEqual(again["gitwin"].count("DPAPI:"), 1)
            # 解密链路同样幂等：解过一次的明文再"解"一次原样返回
            once = js_api._stash_decrypt(again)
            twice = js_api._stash_decrypt(once)
            self.assertEqual(twice, once)
            self.assertEqual(twice["gitwin"], "http://u:p@h:1")

    def test_legacy_plaintext_string_stash_still_loads(self):
        """旧版（未加密）字符串暂存读回原样，保持兼容。"""
        import js_api

        legacy = {"pip": "http://10.0.0.1:8080"}
        self.assertEqual(js_api._stash_decrypt(dict(legacy)), legacy)

    def test_dict_env_and_url_not_double_encrypted(self):
        """dict 形态暂存对已加密值也幂等。"""
        import js_api

        encrypt, decrypt = self._real()
        with mock.patch.object(js_api, "_dpapi_encrypt", encrypt), \
                mock.patch.object(js_api, "_dpapi_decrypt", decrypt):
            first = js_api._stash_encrypt({
                "ps": {"env": {"HTTP_PROXY": "http://u:p@127.0.0.1:7897"}},
                "sys": {"url": "http://u:p@127.0.0.1:7897"},
            })
            again = js_api._stash_encrypt(first)
            self.assertEqual(again, first)


class TestSaveConfigPersistsEncryptedRestore(unittest.TestCase):
    """高危回归（bf9602c 撤销事故）：Api.save_config 把内存中解密态
    self._restore 回写 config.json 时必须先 DPAPI 加密——磁盘上的 restore
    只能是 DPAPI: 密文。全程 mock，不碰真实注册表/环境变量。"""

    def test_save_config_writes_ciphertext_restore(self):
        import os
        import tempfile

        import js_api
        import proxy_manager as pm

        tmp = tempfile.mkdtemp(prefix="pm_test_")
        cfg_file = os.path.join(tmp, "c.json")
        # 真实 DPAPI：保证"磁盘上是密文"的断言有实际区分度。
        encrypt, decrypt = config_store._dpapi_encrypt, config_store._dpapi_decrypt

        secret_url = "http://alice:hunter2@10.0.0.5:8899"
        with mock.patch.object(pm, "winreg"), \
                mock.patch.object(config_store, "CONFIG_FILE", cfg_file), \
                mock.patch("proxy_manager.get_net_counters", return_value=None), \
                mock.patch.object(js_api, "_dpapi_encrypt", encrypt), \
                mock.patch.object(js_api, "_dpapi_decrypt", decrypt):
            api = js_api.Api()
            api._restore = {
                "npm": secret_url,
                "ps": {"env": {"HTTP_PROXY": secret_url, "NO_PROXY": None}},
            }
            self.assertTrue(api.save_config({"host": "127.0.0.1"}))

            with open(cfg_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            restore_on_disk = raw["restore"]
            # 字符串形态：DPAPI 密文，绝无明文 URL/凭据
            self.assertTrue(restore_on_disk["npm"].startswith("DPAPI:"))
            self.assertNotIn("alice", restore_on_disk["npm"])
            self.assertNotIn("hunter2", restore_on_disk["npm"])
            # dict 形态 env 值同样是密文
            self.assertTrue(
                restore_on_disk["ps"]["env"]["HTTP_PROXY"].startswith("DPAPI:"))
            self.assertNotIn("hunter2", restore_on_disk["ps"]["env"]["HTTP_PROXY"])
            # 内存态保持解密明文（后续 disable 恢复路径要用）
            self.assertEqual(api._restore["npm"], secret_url)

            # 新实例从磁盘读回：解密后与内存态一致
            api2 = js_api.Api()
            self.assertEqual(api2._restore["npm"], secret_url)
            self.assertEqual(api2._restore["ps"]["env"]["HTTP_PROXY"], secret_url)
            self.assertIsNone(api2._restore["ps"]["env"]["NO_PROXY"])

    def test_save_config_snapshot_isolated_from_concurrent_stash_insert(self):
        """确定性快照隔离（无并发竞态摆拍）：save_config 在 _cfg_lock 之前
        快照 self._restore——快照之后 toggle 再插入新 key 不影响本次落盘，
        也不会让加密遍历中途遇字典变更抛 RuntimeError。"""
        import os
        import tempfile

        import js_api
        import proxy_manager as pm

        tmp = tempfile.mkdtemp(prefix="pm_test_")
        cfg_file = os.path.join(tmp, "c.json")
        encrypt, decrypt = config_store._dpapi_encrypt, config_store._dpapi_decrypt

        with mock.patch.object(pm, "winreg"), \
                mock.patch.object(config_store, "CONFIG_FILE", cfg_file), \
                mock.patch("proxy_manager.get_net_counters", return_value=None), \
                mock.patch.object(js_api, "_dpapi_encrypt", encrypt), \
                mock.patch.object(js_api, "_dpapi_decrypt", decrypt):
            api = js_api.Api()
            api._restore = {"pip": "http://u:p@10.0.0.1:8080"}
            # save_config 加密/写盘全程在另一个线程持续插入新 key 的字典上
            # 运行是不确定的摆拍；改为在真实时序下重放：先让 save_config
            # 走完（拿到快照），落盘内容不得包含并发插入的 key。
            barrier = threading.Event()

            real_encrypt = js_api._dpapi_encrypt

            def encrypt_then_inject(v):
                # 在加密阶段（快照已拿到、正在遍历副本）模拟 toggle 并发
                # 插入新 key——对快照副本的注入不影响磁盘内容。
                if barrier.is_set():
                    api._restore["gitwin"] = "http://late:p@10.0.0.9:99"
                    barrier.clear()
                return real_encrypt(v)

            with mock.patch.object(js_api, "_dpapi_encrypt",
                                   side_effect=encrypt_then_inject):
                barrier.set()
                self.assertTrue(api.save_config({"host": "127.0.0.1"}))

            with open(cfg_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 快照里只有 pip；加密途中插入的 gitwin 不得出现在本次落盘
            self.assertIn("pip", raw["restore"])
            self.assertNotIn("gitwin", raw["restore"])
            self.assertTrue(raw["restore"]["pip"].startswith("DPAPI:"))

    def test_save_config_holds_mutation_lock_during_encrypt(self):
        """save_config must keep _endpoint_mutation_lock through DPAPI encrypt
        and the disk write. Releasing it after the restore snapshot let a
        concurrent toggle persist a stash that this write then overwrote."""
        import os
        import tempfile

        import js_api
        import proxy_manager as pm

        tmp = tempfile.mkdtemp(prefix="pm_test_")
        cfg_file = os.path.join(tmp, "c.json")
        encrypt, decrypt = config_store._dpapi_encrypt, config_store._dpapi_decrypt
        started = threading.Event()
        release = threading.Event()

        with mock.patch.object(pm, "winreg"), \
                mock.patch.object(config_store, "CONFIG_FILE", cfg_file), \
                mock.patch("proxy_manager.get_net_counters", return_value=None), \
                mock.patch.object(js_api, "_dpapi_decrypt", decrypt):
            api = js_api.Api()
            api._restore = {"npm": "http://u:p@10.0.0.1:1"}

            def slow_encrypt(v):
                started.set()
                release.wait(5)
                return encrypt(v)

            acquired = []

            def saver():
                with mock.patch.object(js_api, "_dpapi_encrypt", slow_encrypt):
                    api.save_config({"host": "127.0.0.1"})

            t = threading.Thread(target=saver, daemon=True)
            t.start()
            self.assertTrue(started.wait(5))
            acquired.append(api._endpoint_mutation_lock.acquire(blocking=False))
            if acquired[0]:
                api._endpoint_mutation_lock.release()
            release.set()
            t.join(5)
            self.assertEqual(acquired, [False])

    def test_save_config_concurrent_toggle_no_runtime_error(self):
        """压力回归（并发缺陷）：一个线程循环模拟 toggle 暂存插入——
        每次插入新 key 并删除最旧 key（字典有界但 size 持续变化，保证
        加密遍历窗口撞上字典变更），另一个线程循环调 save_config。
        save_config 直接遍历 self._restore 的旧实现会抛
        "dictionary changed size during iteration"；快照修复后不抛。
        全 mock，不碰真实注册表/环境变量。"""
        import os
        import tempfile

        import js_api
        import proxy_manager as pm

        tmp = tempfile.mkdtemp(prefix="pm_test_")
        cfg_file = os.path.join(tmp, "c.json")
        encrypt, decrypt = config_store._dpapi_encrypt, config_store._dpapi_decrypt

        with mock.patch.object(pm, "winreg"), \
                mock.patch.object(config_store, "CONFIG_FILE", cfg_file), \
                mock.patch("proxy_manager.get_net_counters", return_value=None), \
                mock.patch.object(js_api, "_dpapi_encrypt", encrypt), \
                mock.patch.object(js_api, "_dpapi_decrypt", decrypt):
            api = js_api.Api()
            stop = threading.Event()
            errors = []

            # toggle 线程：模拟 _stash_previous 的关键段——持 mutation 锁往
            # self._restore 插入新 key 并删除最旧的（字典大小持续变化）。
            # sleep 放锁外：持锁 sleep 会饿死 save_config 的快照取锁。
            def toggler():
                i = 0
                while not stop.is_set():
                    try:
                        with api._endpoint_mutation_lock:
                            i += 1
                            api._restore[f"npm{i}"] = \
                                f"http://u:p@10.0.0.{i % 250}:8{i % 10}"
                            if i > 7:
                                api._restore.pop(f"npm{i - 7}", None)
                        time.sleep(0)  # 让出 GIL，放大与加密遍历的交错
                    except Exception as e:  # pragma: no cover
                        errors.append(f"toggler: {e!r}")
                        return

            t = threading.Thread(target=toggler, daemon=True)
            t.start()
            try:
                for _ in range(150):
                    api.save_config({"host": "127.0.0.1"})
            except RuntimeError as e:
                errors.append(f"save_config: {e!r}")  # 修复前高频复现
            finally:
                stop.set()
                t.join(5)

            self.assertEqual(errors, [])
            # 最终磁盘 restore 仍是密文（DPAPI: 前缀，无明文 URL 残留）
            with open(cfg_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for target, payload in raw["restore"].items():
                if isinstance(payload, str):
                    self.assertTrue(payload.startswith("DPAPI:"),
                                    f"{target} 明文落盘: {payload!r}")
                    self.assertNotIn("u:p@", payload)


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


class TestDpapiFailureLogging(unittest.TestCase):
    """DPAPI 失败时拒绝明文落盘，并留下日志。
    全程 mock，绝不真实触发 DPAPI 失败，也不写真实 crash.log。"""

    def test_encrypt_exception_raises_and_logs(self):
        import proxy_manager as pm

        with mock.patch.object(config_store._crypt32, "CryptProtectData",
                               side_effect=OSError("mock dpapi failure")), \
                mock.patch.object(pm, "log_exception") as fake_log:
            with self.assertRaises(config_store.DpapiError):
                config_store._dpapi_encrypt("secret")
        fake_log.assert_called_once()
        msg = fake_log.call_args[0][0]
        self.assertIn("dpapi_encrypt", msg)
        self.assertIn("拒绝明文", msg)

    def test_encrypt_winapi_false_raises_and_logs(self):
        """CryptProtectData 返回 FALSE 同样拒绝明文落盘。"""
        import proxy_manager as pm

        with mock.patch.object(config_store._crypt32, "CryptProtectData",
                               return_value=0), \
                mock.patch.object(pm, "log_exception") as fake_log:
            with self.assertRaises(config_store.DpapiError):
                config_store._dpapi_encrypt("secret")
        fake_log.assert_called_once()
        msg = fake_log.call_args[0][0]
        self.assertIn("dpapi_encrypt", msg)
        self.assertIn("拒绝明文", msg)

    def test_encrypt_success_does_not_log(self):
        """加密成功路径不得触发失败日志。"""
        import proxy_manager as pm

        with mock.patch.object(config_store._crypt32, "CryptProtectData",
                               return_value=1), \
                mock.patch.object(config_store._kernel32, "LocalFree",
                                  mock.MagicMock()), \
                mock.patch.object(pm, "log_exception") as fake_log:
            out = config_store._dpapi_encrypt("secret")
        fake_log.assert_not_called()
        self.assertTrue(out.startswith("DPAPI:"))
        self.assertNotIn("secret", out)

    def test_decrypt_exception_falls_back_to_stored_and_logs(self):
        """解密失败保留原样返回的兼容行为，仅新增日志，不改变行为。"""
        import proxy_manager as pm

        stored = "DPAPI:AAAA"
        with mock.patch.object(config_store._crypt32, "CryptUnprotectData",
                               side_effect=OSError("mock dpapi failure")), \
                mock.patch.object(pm, "log_exception") as fake_log:
            self.assertEqual(config_store._dpapi_decrypt(stored), stored)
        fake_log.assert_called_once()
        msg = fake_log.call_args[0][0]
        self.assertIn("dpapi_decrypt", msg)

    def test_decrypt_winapi_false_falls_back_to_stored_and_logs(self):
        import proxy_manager as pm

        stored = "DPAPI:AAAA"
        with mock.patch.object(config_store._crypt32, "CryptUnprotectData",
                               return_value=0), \
                mock.patch.object(pm, "log_exception") as fake_log:
            self.assertEqual(config_store._dpapi_decrypt(stored), stored)
        fake_log.assert_called_once()
        self.assertIn("dpapi_decrypt", fake_log.call_args[0][0])

    def test_save_config_does_not_write_plaintext_on_encrypt_failure(self):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg_path = Path(tmp.name) / "config.json"
        (Path(tmp.name) / "config.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(config_store, "CONFIG_FILE", str(cfg_path)), \
                mock.patch.object(config_store, "APP_DIR", tmp.name), \
                mock.patch.object(config_store, "_dpapi_encrypt",
                                  side_effect=config_store.DpapiError("nope")):
            with self.assertRaises(config_store.DpapiError):
                config_store.save_config({"password": "super-secret", "host": "127.0.0.1"})
        text = cfg_path.read_text(encoding="utf-8")
        self.assertNotIn("super-secret", text)

    def test_crypt_apis_have_64bit_argtypes(self):
        """DATA_BLOB pointers must not be truncated on Win64."""
        self.assertTrue(config_store._crypt32.CryptProtectData.argtypes)
        self.assertTrue(config_store._crypt32.CryptUnprotectData.argtypes)
        self.assertTrue(config_store._kernel32.LocalFree.argtypes)


if __name__ == "__main__":
    unittest.main()
