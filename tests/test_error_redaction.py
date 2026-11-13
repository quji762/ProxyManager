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


def test_safe_error_redacts_userinfo_in_other_exceptions():
    msg = pm.safe_error(OSError(f"connect failed for {CRED_URL}"))
    assert SECRET not in msg
    assert CRED_URL not in msg
    assert "http://***@" in msg


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


def test_log_exception_redacts_subprocess_argv(tmp_path, monkeypatch):
    """crash.log must never receive the subprocess argv (it embeds the URL)."""
    log = tmp_path / "crash.log"
    monkeypatch.setattr(pm, "_CRASH_LOG", str(log))
    try:
        raise subprocess.TimeoutExpired(
            ["git", "config", "--replace-all", "http.proxy", CRED_URL], 15)
    except subprocess.TimeoutExpired:
        pm.log_exception("unit")
    text = log.read_text(encoding="utf-8")
    assert SECRET not in text
    assert CRED_URL not in text
    assert "TimeoutExpired" in text          # still diagnosable


def test_log_crash_redacts_subprocess_argv(tmp_path, monkeypatch):
    log = tmp_path / "crash.log"
    monkeypatch.setattr(pm, "_CRASH_LOG", str(log))
    e = subprocess.CalledProcessError(1, ["git", "config", CRED_URL])
    pm.log_crash(type(e), e, e.__traceback__)
    text = log.read_text(encoding="utf-8")
    assert SECRET not in text
    assert CRED_URL not in text
    assert "CalledProcessError" in text


def test_log_exception_keeps_non_subprocess_message(tmp_path, monkeypatch):
    log = tmp_path / "crash.log"
    monkeypatch.setattr(pm, "_CRASH_LOG", str(log))
    try:
        raise OSError("disk full")
    except OSError:
        pm.log_exception("unit")
    text = log.read_text(encoding="utf-8")
    assert "OSError: disk full" in text


def test_log_exception_redacts_embedded_url_credentials(tmp_path, monkeypatch):
    """A non-subprocess message that embeds a credentialed URL is redacted."""
    log = tmp_path / "crash.log"
    monkeypatch.setattr(pm, "_CRASH_LOG", str(log))
    try:
        raise OSError(f"connect failed for {CRED_URL}")
    except OSError:
        pm.log_exception("unit")
    text = log.read_text(encoding="utf-8")
    assert SECRET not in text
    assert CRED_URL not in text
    assert "http://***@" in text


def test_curl_probe_timeout_does_not_leak():
    """A curl timeout's argv contains -x <proxy-url>. The chip must not."""
    from js_api import Api
    api = Api()

    def boom(cmd, **_kw):
        raise subprocess.TimeoutExpired(cmd, 8)

    with patch("js_api.subprocess.run", side_effect=boom):
        r = api._curl_probe("curl", CRED_URL)
    assert SECRET not in str(r)
    assert CRED_URL not in str(r)
    assert r.get("error_key") == "errTimeout"


def test_wsl_proxy_probe_timeout_does_not_leak():
    from js_api import Api
    api = Api()

    def boom(cmd, **_kw):
        raise subprocess.TimeoutExpired(cmd, 25)

    with patch("js_api.subprocess.run", side_effect=boom):
        r = api._wsl_proxy_probe(CRED_URL, {"scheme": "http"})
    assert SECRET not in str(r)
    assert CRED_URL not in str(r)
    assert r.get("error_key") == "errTimeout"


def test_urllib_probe_redacts_embedded_proxy_url():
    import time
    import urllib.request

    from js_api import Api
    api = Api()

    class _Opener:
        def open(self, *_a, **_k):
            raise OSError(f"proxy refused {CRED_URL}")

    class _URLReq:
        ProxyHandler = staticmethod(lambda _d: object())
        build_opener = staticmethod(lambda _proxy: _Opener())
        Request = urllib.request.Request

    with patch("shutil.which", return_value=None):
        r = api._proxy_probe(CRED_URL, {"scheme": "http"}, time, _URLReq)
    assert SECRET not in str(r)
    assert CRED_URL not in str(r)
    assert "http://***@" in r.get("error", "")


def test_restart_wsl_timeout_does_not_leak():
    e = subprocess.TimeoutExpired(["wsl", "--shutdown", CRED_URL], 15)
    with patch.object(pm.subprocess, "run", side_effect=e):
        r = pm.restart_wsl()
    assert isinstance(r, LStr)
    assert r.key == "errTimeout"
    assert SECRET not in str(r)
    assert CRED_URL not in str(r)


def test_log_exception_escapes_newlines(tmp_path, monkeypatch):
    """A newline in an exception message must not forge a second log record."""
    log = tmp_path / "crash.log"
    monkeypatch.setattr(pm, "_CRASH_LOG", str(log))
    try:
        raise ValueError("line1\n[2026] forged entry")
    except ValueError:
        pm.log_exception("unit")
    text = log.read_text(encoding="utf-8")
    assert "line1\\n[2026] forged entry" in text
    assert "\n[2026] forged entry" not in text