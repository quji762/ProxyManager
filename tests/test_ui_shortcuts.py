"""Static contract for the global keyboard shortcuts in ui/app.js."""

from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "ui" / "app.js").read_text(
    encoding="utf-8")

MARKER = "Global shortcuts: digits 1-9 pick the matching port preset"


def _block():
    i = APP.index(MARKER)
    return APP[i:i + 1400]


def test_digit_shortcut_switches_port_preset():
    block = _block()
    assert "/^[1-9]$/" in block
    assert "Number(e.key) - 1" in block
    assert "const ports = cfg.ports || [];" in block
    assert "quickSwitchPort(ports[idx])" in block


def test_shortcuts_ignored_while_typing_or_modal():
    block = _block()
    assert "e.ctrlKey || e.metaKey || e.altKey" in block
    assert "input, textarea, select" in block
    assert "contenteditable" in block
    assert "_dlgOpen" in block
    assert "settingsOv" in block


def test_escape_closes_port_and_preset_popovers():
    block = _block()
    assert '$("portPop").classList.remove("show")' in block
    assert '$("profilePop").classList.remove("show")' in block


def test_port_chip_tooltip_shows_the_shortcut_number():
    assert "cfg.ports.map((p, i) =>" in APP
    assert 'i < 9 ? " (" + (i + 1) + ")" : ""' in APP
