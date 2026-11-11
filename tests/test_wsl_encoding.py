"""`wsl -l -q` output decoding must not assume UTF-16LE."""

import proxy_manager as pm


def test_utf16le_with_bom():
    raw = "\ufeffUbuntu\r\n".encode("utf-16-le")     # encodes the BOM prefix
    assert pm._decode_wsl_list_output(raw) == "Ubuntu\r\n"


def test_utf16le_without_bom():
    raw = "Ubuntu-22.04\n".encode("utf-16-le")
    assert pm._decode_wsl_list_output(raw) == "Ubuntu-22.04\n"


def test_utf16be_with_bom():
    raw = "\ufeffDebian\n".encode("utf-16-be")
    assert pm._decode_wsl_list_output(raw) == "Debian\n"


def test_utf8_output_is_not_mangled():
    raw = b"Ubuntu\nDebian\n"
    assert pm._decode_wsl_list_output(raw) == "Ubuntu\nDebian\n"


def test_utf8_bom_output_strips_bom():
    raw = "Ubuntu\n".encode("utf-8-sig")
    assert pm._decode_wsl_list_output(raw) == "Ubuntu\n"


def test_wsl_distros_parses_a_utf8_listing(monkeypatch):
    class R:
        returncode = 0
        stdout = b"Ubuntu\nDebian\ndocker-desktop\n"
        stderr = b""

    monkeypatch.setattr(pm, "_wsl_distros_cache", None)
    monkeypatch.setattr(pm.subprocess, "run", lambda *a, **k: R())
    assert pm._wsl_distros() == ["Ubuntu", "Debian"]
