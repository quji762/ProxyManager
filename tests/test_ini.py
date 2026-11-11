"""Surgical INI editing for npm/pip config files."""

import proxy_manager as pm
from proxy_manager import _eol_of, _ini_edit, _ini_get


def test_ini_get_quoted_and_equals():
    text = 'proxy = "http://127.0.0.1:7897"\nhttps-proxy: socks5://127.0.0.1:1080\n'
    assert _ini_get(text, "proxy") == "http://127.0.0.1:7897"
    assert _ini_get(text, "https-proxy") == "socks5://127.0.0.1:1080"
    assert _ini_get(text, "missing") is None


def test_ini_edit_sets_and_preserves_comments():
    text = "# keep me\nregistry=https://registry.npmjs.org\nproxy = http://old:1\n"
    new = _ini_edit(text, ("proxy", "https-proxy"), "http://127.0.0.1:7897",
                    equals="http://old:1")
    assert "# keep me" in new
    assert "registry=https://registry.npmjs.org" in new
    assert _ini_get(new, "proxy") == "http://127.0.0.1:7897"
    assert _ini_get(new, "https-proxy") == "http://127.0.0.1:7897"


def test_ini_edit_surgical_remove_keeps_foreign_value():
    text = "proxy = http://other:9\nhttps-proxy = http://127.0.0.1:7897\n"
    new = _ini_edit(text, ("proxy", "https-proxy"), None,
                    equals="http://127.0.0.1:7897")
    assert _ini_get(new, "proxy") == "http://other:9"
    assert _ini_get(new, "https-proxy") is None


def test_ini_edit_disable_keeps_empty_valued_line():
    """An empty `proxy =` line is not ours (we always write a non-empty URL):
    disable must preserve it verbatim, not drop it just because it is empty."""
    text = "proxy =\nhttps-proxy = http://127.0.0.1:7897\n"
    new = _ini_edit(text, ("proxy", "https-proxy"), None,
                    equals="http://127.0.0.1:7897")
    assert "proxy =\n" in new
    assert _ini_get(new, "https-proxy") is None


def test_ini_edit_section_append():
    text = "[global]\ntimeout = 15\n"
    new = _ini_edit(text, ("proxy",), "http://127.0.0.1:7897",
                    equals="", section="global")
    assert "[global]" in new
    assert _ini_get(new, "proxy") == "http://127.0.0.1:7897"
    assert "timeout = 15" in new


def test_eol_of():
    assert _eol_of("a\r\nb\r\n") == "\r\n"
    assert _eol_of("a\nb\n") == "\n"


def test_get_npm_proxy_falls_back_to_https_proxy(tmp_path, monkeypatch):
    """A .npmrc with only https-proxy must still count as configured —
    otherwise enable overwrites it without stashing and disable drops it."""
    npmrc = tmp_path / ".npmrc"
    npmrc.write_text("https-proxy = http://old:1\n", encoding="utf-8")
    monkeypatch.setattr(pm, "NPM_CONFIG_PATH", str(npmrc))
    assert pm.get_npm_proxy() == "http://old:1"
