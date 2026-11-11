"""INI edge cases: BOM, BOM-less UTF-16, CRLF, empty files, duplicate keys.

These protect the "edit the real line, never append a duplicate" contract that
the npm/pip Windows and WSL writers all rely on.
"""

import proxy_manager as pm


def test_utf8_bom_roundtrips_without_duplicate():
    data = b"\xef\xbb\xbfproxy = http://old:1\n"
    text, enc = pm._decode_ini_bytes(data)
    assert text == "proxy = http://old:1\n"          # BOM stripped for matching
    out = pm._ini_edit(text, ["proxy"], "http://new:2", equals=None)
    assert out == "proxy = http://new:2\n"           # real line edited, no dup
    assert out.encode(enc) == b"\xef\xbb\xbfproxy = http://new:2\n"


def test_bomless_utf16le_is_detected_and_edited_in_place():
    text0 = "proxy = http://old:1\n"
    data = text0.encode("utf-16-le")                 # no BOM
    text, enc = pm._decode_ini_bytes(data)
    assert text == text0                             # not NUL-interleaved
    assert enc == "utf-16-le"
    out = pm._ini_edit(text, ["proxy"], "http://new:2", equals=None)
    assert out == "proxy = http://new:2\n"
    assert out.encode(enc) == "proxy = http://new:2\n".encode("utf-16-le")


def test_bomless_utf16be_is_detected():
    data = "proxy = http://old:1\n".encode("utf-16-be")
    text, enc = pm._decode_ini_bytes(data)
    assert text == "proxy = http://old:1\n"
    assert enc == "utf-16-be"


def test_crlf_line_endings_preserved_on_edit():
    text = "registry=https://r/\r\nproxy = http://old:1\r\n"
    out = pm._ini_edit(text, ["proxy"], "http://new:2", equals=None)
    assert out == "registry=https://r/\r\nproxy = http://new:2\r\n"


def test_empty_file_gets_the_key_appended():
    out = pm._ini_edit("", ["proxy"], "http://new:2", equals=None, eol="\n")
    assert out == "proxy = http://new:2\n"


def test_duplicate_keys_are_all_rewritten_on_enable():
    text = "proxy = http://a:1\nproxy = http://b:2\n"
    out = pm._ini_edit(text, ["proxy"], "http://new:3", equals=None)
    assert out == "proxy = http://new:3\nproxy = http://new:3\n"


def test_disable_removes_only_the_matching_duplicate():
    text = "proxy = http://user:1\nproxy = http://ours:2\n"
    out = pm._ini_edit(text, ["proxy"], None, equals="http://ours:2")
    assert out == "proxy = http://user:1\n"


def test_disable_keeps_empty_valued_line():
    out = pm._ini_edit("proxy =\n", ["proxy"], None, equals="http://ours:2")
    assert out == "proxy =\n"
