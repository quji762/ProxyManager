"""Static (no JS runtime) contract checks for the R6 accessibility work.

The repo has no JS test harness — CI runs ruff + pytest only — so these parse
ui/index.html, ui/app.js and ui/i18n.js with regex, the same way
tests/test_ui_applied_contract.py and tests/test_ui_feedback.py do.

Scope:
  * modal dialogs expose role/aria-modal and an accessible name;
  * the toast is a polite live region and the tooltip has role=tooltip;
  * the proxy form labels are programmatically associated with their inputs;
  * the Win/WSL tablist follows the tab pattern (roles, aria-controls, a
    tabpanel, roving tabindex) and the panel is labelled by the active tab;
  * endpoint switches carry an accessible name and survive re-render focus;
  * a modal focus trap is wired and the settings dialog closes on Escape;
  * the new aria-label i18n hook exists and its key is in both language tables.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
I18N_JS = (ROOT / "ui" / "i18n.js").read_text(encoding="utf-8")


def _tag_with_id(markup, el_id):
    """The opening tag of the element carrying id="el_id"."""
    m = re.search(r"<[^>]*\bid=\"" + re.escape(el_id) + r"\"[^>]*>", markup)
    assert m, f"no element with id={el_id}"
    return m.group(0)


# ── dialogs ─────────────────────────────────────────────────────────────────
def test_confirm_dialog_is_named_modal():
    # The dialog card is the labelledby/describedby target's container; find
    # the role=dialog card that references dlgTitleText.
    m = re.search(r'<div class="card dialog"[^>]*role="dialog"[^>]*>', HTML)
    assert m, "confirm dialog card must have role=dialog"
    tag = m.group(0)
    assert 'aria-modal="true"' in tag
    assert 'aria-labelledby="dlgTitleText"' in tag
    assert 'aria-describedby="dlgText"' in tag
    assert 'id="dlgTitleText"' in HTML and 'id="dlgText"' in HTML


def test_settings_dialog_is_named_modal():
    m = re.search(r'<div class="card dialog settings"[^>]*role="dialog"[^>]*>', HTML)
    assert m, "settings dialog card must have role=dialog"
    tag = m.group(0)
    assert 'aria-modal="true"' in tag
    assert 'aria-labelledby="settingsTitle"' in tag
    assert 'id="settingsTitle"' in HTML


def test_toast_is_polite_live_region():
    tag = _tag_with_id(HTML, "toast")
    assert 'role="status"' in tag
    assert 'aria-live="polite"' in tag
    assert 'aria-atomic="true"' in tag


def test_tooltip_has_role():
    assert 'role="tooltip"' in _tag_with_id(HTML, "tooltip")


# ── form label association ──────────────────────────────────────────────────
def test_form_labels_associated_with_inputs():
    for field in ("inScheme", "inHost", "inPort", "inUser", "inPass"):
        assert f'id="{field}"' in HTML, f"missing input {field}"
        assert re.search(r'<label[^>]*\bfor="' + field + r'"', HTML), \
            f"no <label for={field}>"


# ── tablist / tabpanel ──────────────────────────────────────────────────────
def test_tablist_has_accessible_name():
    tag = _tag_with_id(HTML, "laneTabs")
    assert 'role="tablist"' in tag
    assert "data-i18n-aria=" in tag or "aria-label=" in tag


def test_tabs_control_the_endpoint_panel():
    for tab in ("tabWin", "tabWsl"):
        tag = _tag_with_id(HTML, tab)
        assert 'role="tab"' in tag
        assert 'aria-controls="epList"' in tag
        assert "aria-selected=" in tag
        assert "tabindex=" in tag


def test_endpoint_list_is_a_labelled_tabpanel():
    tag = _tag_with_id(HTML, "epList")
    assert 'role="tabpanel"' in tag
    assert 'aria-labelledby="tabWin"' in tag


def test_setlane_updates_roving_tabindex_and_panel_label():
    # Roving tabindex: the active tab gets 0, the other -1.
    assert re.search(r'\$\("tabWin"\)\.tabIndex\s*=\s*lane === "win" \? 0 : -1', APP_JS)
    assert re.search(r'\$\("tabWsl"\)\.tabIndex\s*=\s*lane === "wsl" \? 0 : -1', APP_JS)
    # The shared panel is re-labelled to whichever tab is active.
    assert 'host.setAttribute("aria-labelledby"' in APP_JS


# ── endpoint switches ───────────────────────────────────────────────────────
def test_endpoint_switch_has_accessible_name():
    m = re.search(r'<button[^>]*class="tgl[^>]*role="switch"[^>]*>', APP_JS)
    assert m, "endpoint switch button not found"
    assert "aria-label=" in m.group(0)
    assert "aria-checked=" in m.group(0)


def test_render_endpoints_preserves_switch_focus():
    start = APP_JS.index("function renderEndpoints()")
    block = APP_JS[start:APP_JS.index("function setLane", start)]
    assert "document.activeElement" in block
    assert "focusId" in block
    assert ".focus()" in block


# ── focus trap / escape ─────────────────────────────────────────────────────
def test_modal_focus_trap_wired():
    assert "function _trapFocus(" in APP_JS
    assert 'document.addEventListener("keydown", _trapFocus)' in APP_JS
    assert 'e.key !== "Tab"' in APP_JS


def test_settings_closes_on_escape_and_restores_focus():
    assert "_settingsPrevFocus" in APP_JS
    assert re.search(r'Escape.*settingsOv.*closeSettings', APP_JS)
    assert "$(\"settingsClose\").focus()" in APP_JS


# ── i18n hook ───────────────────────────────────────────────────────────────
def test_aria_label_i18n_hook_present():
    assert "data-i18n-aria" in I18N_JS
    assert re.search(r'querySelectorAll\("\[data-i18n-aria\]"\)', I18N_JS)
    assert 'setAttribute("aria-label"' in I18N_JS


def test_lane_tabs_label_key_in_both_languages():
    for lang in ("zh", "en"):
        m = re.search(rf"^\s*{lang}:\s*\{{", I18N_JS, re.MULTILINE)
        assert m, f"missing {lang} table"
        # Bound to this table: top-level tables close with a 2-space "},".
        rest = I18N_JS[m.end():]
        close = re.search(r"\n  \},", rest)
        body = rest[:close.start()] if close else rest
        assert re.search(r"\blaneTabsLabel:\s*\"", body), \
            f"laneTabsLabel missing from {lang}"