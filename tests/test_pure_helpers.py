"""Unit tests for small pure helpers that had no direct coverage.

They are the building blocks behind path translation, INI value parsing and
credential masking; a regression here would silently corrupt writes or leak
a password into an error toast, so each gets explicit boundary coverage.
"""

import js_api
import proxy_manager as pm
import window_win32


def test_signed16_boundaries():
    assert window_win32._signed16(0) == 0
    assert window_win32._signed16(1) == 1
    assert window_win32._signed16(0x7FFF) == 0x7FFF
    assert window_win32._signed16(0x8000) == -0x8000
    assert window_win32._signed16(0xFFFF) == -1


def test_bash_path_expands_only_a_leading_tilde():
    assert pm._bash_path("~/.npmrc") == "$HOME/.npmrc"
    assert pm._bash_path("~") == "$HOME"
    assert pm._bash_path("~/a~b") == "$HOME/a~b"      # a later ~ is untouched
    assert pm._bash_path("/etc/pip.conf") == "/etc/pip.conf"
    assert pm._bash_path("a~/b") == "a~/b"            # not a leading tilde


def test_ini_value_comment_split():
    assert pm._ini_value_comment("http://h:1") == ("http://h:1", "")
    value, comment = pm._ini_value_comment("http://h:1  # corp")
    assert value == "http://h:1  "
    assert comment == "# corp"
    # '#' inside a value (no preceding whitespace) is not a comment marker
    assert pm._ini_value_comment("http://h:1/p#frag")[1] == ""
    assert pm._ini_value_comment("; lead")[0] == ""


def test_ini_strip_value_handles_quotes_comments_and_empty():
    assert pm._ini_strip_value('"http://h:1"') == "http://h:1"
    assert pm._ini_strip_value("'http://h:1'") == "http://h:1"
    assert pm._ini_strip_value("http://h:1  # c") == "http://h:1"
    assert pm._ini_strip_value("   ") is None
    assert pm._ini_strip_value("''") is None


def test_mask_userinfo_redacts_credentials():
    assert js_api._mask_userinfo(
        "http://user:pass@127.0.0.1:7890") == "http://***@127.0.0.1:7890"
    assert js_api._mask_userinfo("socks5://u:p@h:1") == "socks5://***@h:1"
    assert js_api._mask_userinfo("http://h:1") == "http://h:1"
    assert js_api._mask_userinfo("u:p@h:1") == "***@h:1"
