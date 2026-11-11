"""Static contract checks for R8: the browser mock must stay faithful to the
real js_api backend, so designing/testing the UI standalone reflects reality.

The repo has no JS runtime harness — CI runs ruff + pytest only — so these
parse ui/app.js (the mock switch + every api.call site) and js_api.py with
regex, the same way tests/test_ui_feedback.py does.

Scope:
  * every method the UI calls has a mock case AND exists on the real Api;
  * get_config carries the applied record / version / wsl_written the boot
    path reads (A2: no applied record => the tool never wrote);
  * pick_background returns a path (the UI mirrors r.path), refresh_item("sys")
    reads the independent WinINET value rather than the PowerShell one,
    apply_form_back is handled, get_speed carries the probed port;
  * background preferences round-trip through the mock like save_config does.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
JS_API = (ROOT / "js_api.py").read_text(encoding="utf-8")


def _called_methods():
    return set(re.findall(r'api\.call\(\s*["\']([^"\']+)["\']', APP_JS))


def _mock_cases():
    return set(re.findall(r'case\s+["\']([^"\']+)["\']\s*:', APP_JS))


def _backend_methods():
    return set(re.findall(r"^    def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*self",
                          JS_API, re.MULTILINE))


def _case_body(name):
    m = re.search(r'case\s+"' + re.escape(name) + r'"\s*:(.*?)(?=\n\s*case\s+"|\n\s*default:)',
                  APP_JS, re.DOTALL)
    assert m, f"mock has no case for {name}"
    return m.group(1)


# ── coverage ────────────────────────────────────────────────────────────────
def test_every_ui_call_has_a_mock_case():
    missing = _called_methods() - _mock_cases()
    assert not missing, f"UI calls with no mock case: {sorted(missing)}"


def test_every_ui_call_exists_in_backend():
    missing = _called_methods() - _backend_methods()
    assert not missing, f"UI calls absent from js_api.Api: {sorted(missing)}"


# ── return-shape fidelity ───────────────────────────────────────────────────
def test_mock_get_config_carries_applied_record_and_version():
    body = _case_body("get_config")
    for field in ("version", "applied_host", "applied_port", "applied_scheme",
                  "applied_user", "applied_password", "wsl_written", "config_load"):
        assert field in body, f"get_config mock missing {field}"


def test_mock_pick_background_returns_path():
    body = _case_body("pick_background")
    assert "path:" in body or "path =" in body
    # The UI mirrors r.path into cfg.bg_path; a name-only reply breaks it.
    assert "mockState.bg_path" in body


def test_mock_sys_is_independent_of_ps():
    refresh = _case_body("refresh_item")
    assert re.search(r'w === "sys"\)\s*return mockState\.sysVal', refresh)
    # The sys branch must not derive its value from the PowerShell state.
    assert not re.search(r'w === "sys"\)\s*return mockState\.ps', refresh)
    toggle = _case_body("do_toggle")
    assert re.search(r'target === "sys".*mockState\.sysVal', toggle)


def test_mock_apply_form_back_is_handled():
    assert "apply_form_back" in _mock_cases()


def test_mock_get_speed_carries_port():
    assert "port:" in _case_body("get_speed")


def test_mock_background_prefs_round_trip():
    cfg = _case_body("get_config")
    assert "bg_path: mockState.bg_path" in cfg
    assert "bg_dim: mockState.bg_dim" in cfg
    assert "card_alpha: mockState.card_alpha" in cfg
    assert 'mockState.bg_path = ""' in _case_body("clear_background")


def test_mock_records_applied_on_toggle_and_switch():
    toggle = _case_body("do_toggle")
    assert "mockState.appliedHost" in toggle and "mockState.wslWritten" in toggle
    switch = _case_body("switch_port")
    assert "mockState.appliedHost" in switch and "mockState.wslWritten" in switch