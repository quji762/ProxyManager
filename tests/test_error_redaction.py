"""Subprocess error text must never leak the proxy URL's credentials.

TimeoutExpired / CalledProcessError str() embeds the full argv, which carries
the proxy URL incl. userinfo. proxy_manager.safe_error maps those to a fixed
localized message; the endpoint setters route subprocess failures through it
so a timeout toast (or screenshot/log) cannot expose the password.
"""

import subprocess
from unittest.mock import patch

import proxy_manager as pm
from messages import LStr

SECRET = "s3cr3t-pass"
CRED_URL = f"http://user:{SECRET}@127.0.0.1:7890"


def test_safe_error_redacts_timeout():
    e = subprocess.TimeoutExpired(
        ["git", "config", "--replace-all", "http.proxy", CRED_URL], 15)
    msg = pm.safe_error(e)
    assert isinstance(msg, LStr)
    assert msg.key == "errTimeout"
    assert SECRET not in str(msg)
    assert CRED_URL not in str(msg)


def test_safe_error_redacts_called_process_error():
    e = subprocess.CalledProcessError(1, ["git", "config", CRED_URL])
    msg = pm.safe_error(e)
    assert isinstance(msg, LStr)
    assert msg.key == "errOpFail"
    assert SECRET not in str(msg)


def test_safe_error_passes_through_non_subprocess():
    # winreg/OSError carry no argv; their text is informative and safe.
    assert pm.safe_error(OSError("disk full")) == "disk full"


def test_set_git_proxy_windows_timeout_does_not_leak():
    def fake_run(cmd, **kw):
        if "--replace-all" in cmd:
            raise subprocess.TimeoutExpired(cmd, 15)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    with patch.object(pm.subprocess, "run", side_effect=fake_run):
        r = pm.set_git_proxy("127.0.0.1", "7890", True,
                             user="user", password=SECRET)
    assert r is not True
    assert SECRET not in str(r)
    assert CRED_URL not in str(r)


def test_set_npm_proxy_wsl_timeout_does_not_leak():
    e = subprocess.TimeoutExpired(
        ["wsl", "-d", "Ubuntu", "-e", "bash", "-c", CRED_URL], 15)
    with patch.object(pm, "_wsl_edit_ini", side_effect=e):
        r = pm.set_npm_proxy_wsl("127.0.0.1", "7890", True,
                                 user="user", password=SECRET,
                                 distros=["Ubuntu"])
    assert r is not True
    assert SECRET not in str(r)
    assert CRED_URL not in str(r)


def test_set_pip_proxy_wsl_timeout_does_not_leak():
    e = subprocess.TimeoutExpired(
        ["wsl", "-d", "Ubuntu", "-e", "bash", "-c", CRED_URL], 15)
    with patch.object(pm, "_wsl_edit_ini", side_effect=e):
        r = pm.set_pip_proxy_wsl("127.0.0.1", "7890", True,
                                 user="user", password=SECRET,
                                 distros=["Ubuntu"])
    assert r is not True
    assert SECRET not in str(r)
    assert CRED_URL not in str(r)