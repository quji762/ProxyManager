"""pip.ini edits are [global]-scoped: pip ignores an [install] proxy, so one
must neither be rewritten on enable/disable nor make the UI report enabled.
Regression: _ini_edit matched key lines regardless of section."""

from proxy_manager import _ini_edit, _ini_get
import proxy_manager as pm


def test_ini_edit_enable_touches_global_only():
    text = "[install]\nproxy = x\n[global]\ntimeout = 15\n"
    new = _ini_edit(text, ("proxy",), "http://127.0.0.1:7897",
                    equals="", section="global")
    assert "[install]\nproxy = x\n" in new          # foreign section verbatim
    assert "timeout = 15" in new
    assert _ini_get(new, "proxy", section="global") == "http://127.0.0.1:7897"


def test_ini_edit_disable_touches_global_only():
    text = "[global]\nproxy = http://127.0.0.1:7897\n[install]\nproxy = x\n"
    new = _ini_edit(text, ("proxy",), None,
                    equals="http://127.0.0.1:7897", section="global")
    assert "127.0.0.1:7897" not in new              # our value removed
    assert "[install]\nproxy = x\n" in new          # foreign value kept


def test_ini_edit_creates_global_section_when_missing():
    text = "[install]\nproxy = x\n"
    new = _ini_edit(text, ("proxy",), "http://127.0.0.1:7897",
                    equals="", section="global")
    assert _ini_get(new, "proxy", section="global") == "http://127.0.0.1:7897"
    assert "[install]\nproxy = x\n" in new


def test_ini_get_section_reads_only_that_section():
    text = "[install]\nproxy = a\n[global]\nproxy = b\n"
    assert _ini_get(text, "proxy", section="global") == "b"
    assert _ini_get(text, "proxy", section="install") == "a"
    assert _ini_get(text, "proxy", section="missing") is None
    assert _ini_get(text, "proxy") == "a"           # flat read unchanged


def test_get_pip_proxy_reads_global_only(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    ini = tmp_path / "pip" / "pip.ini"
    ini.parent.mkdir()
    ini.write_text("[install]\nproxy = http://ignored:1\n[global]\n"
                   "proxy = http://127.0.0.1:7897\n", encoding="utf-8")
    assert pm.get_pip_proxy() == "http://127.0.0.1:7897"


def test_get_pip_proxy_ignores_install_section(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    ini = tmp_path / "pip" / "pip.ini"
    ini.parent.mkdir()
    ini.write_text("[install]\nproxy = http://ignored:1\n", encoding="utf-8")
    assert pm.get_pip_proxy() == pm.UNSET


def test_set_pip_proxy_enable_writes_global_only(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    ini = tmp_path / "pip" / "pip.ini"
    ini.parent.mkdir()
    ini.write_text("[install]\nproxy = x\n[global]\ntimeout = 15\n",
                   encoding="utf-8")
    assert pm.set_pip_proxy("127.0.0.1", "7897") is True
    new = ini.read_text(encoding="utf-8")
    assert "[install]\nproxy = x\n" in new
    assert _ini_get(new, "proxy", section="global") == "http://127.0.0.1:7897"
