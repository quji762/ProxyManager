# -*- coding: utf-8 -*-
"""Static (no JS runtime) contract checks for named address presets and
config backup / one-click restore.

CI runs ruff + pytest only — no JS harness — so ui/app.js / ui/i18n.js /
ui/index.html are parsed with brace/quote-aware scanners, like
tests/test_ui_pac_update.py.

Contracts:
  * applying a preset REUSES applyAddress → switch_port (no new ownership
    logic): the applied/restore semantics stay identical to a port switch;
  * restore refills the form but must NEVER call applyAddress — endpoints
    keep their live ownership, so a stale backup cannot make quit-clear
    claim the user's current proxy;
  * preset names / backup names are user/storage data and reach innerHTML
    only through esc_();
  * all new i18n keys exist in both language tables, without duplicates.
"""

import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
APP_JS = UI_DIR / "app.js"
I18N_JS = UI_DIR / "i18n.js"
INDEX_HTML = UI_DIR / "index.html"

NEW_KEYS = ("presetManage", "presetNamePlaceholder", "presetSave",
            "tipManagePresets", "tipApplyPreset", "presetSaved", "presetDeleted",
            "presetBadName", "presetMissing", "backupTitle", "backupDesc",
            "backupNow", "backupDone", "backupFail", "backupDeleted",
            "backupMissing", "noBackups", "restore", "delete",
            "confirmRestore", "restoreDone")


def _read(path):
    return path.read_text(encoding="utf-8")


# ── scanners (same shape as tests/test_ui_pac_update.py) ────────────────────
def _skip_string(src, i):
    quote = src[i]
    i += 1
    n = len(src)
    while i < n:
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == quote:
            return i + 1
        i += 1
    raise AssertionError("unterminated string literal")


def _js_block(src, header_regex):
    m = re.search(header_regex, src)
    assert m, f"no JS function matching {header_regex}"
    i = src.index("{", m.start())
    depth, j, n = 0, i, len(src)
    while j < n:
        c = src[j]
        if c in "\"'`":
            j = _skip_string(src, j)
            continue
        if c == "/" and j + 1 < n and src[j + 1] == "/":
            j = src.index("\n", j)
        elif c == "/" and j + 1 < n and src[j + 1] == "*":
            j = src.index("*/", j) + 2
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces after {header_regex}")


def _i18n_entries(src, lang):
    m = re.search(rf"^\s*{lang}:\s*\{{", src, re.MULTILINE)
    assert m, f"{lang} table not found in ui/i18n.js"
    i = m.end() - 1
    depth, j, n = 0, i, len(src)
    while j < n:
        c = src[j]
        if c in "\"'`":
            j = _skip_string(src, j)
            continue
        if c == "/" and j + 1 < n and src[j + 1] == "*":
            j = src.index("*/", j) + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    body = src[i + 1:j]
    entries = []
    k, ln = 0, len(body)
    while k < ln:
        while k < ln and body[k] in " \t\r\n,":
            k += 1
        if k >= ln:
            break
        if body[k:k + 2] == "/*":
            k = body.index("*/", k) + 2
            continue
        km = re.match(r'(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_.]*))\s*:', body[k:])
        assert km, f"{lang}: unparsable entry at {body[k:k + 60]!r}"
        key = km.group(1) or km.group(2)
        k += km.end()
        while k < ln and body[k] in " \t":
            k += 1
        assert body[k] == '"', f"{lang}.{key}: value is not a string literal"
        k += 1
        start = k
        while k < ln and body[k] != '"':
            k += 2 if body[k] == "\\" else 1
        entries.append((key, body[start:k]))
        k += 1
    return entries


# ── presets: reuse the switch machinery, escape user data ───────────────────
def test_apply_preset_reuses_apply_address():
    fn = _js_block(_read(APP_JS), r"async function applyPreset\s*\(")
    assert 'api.call("get_preset"' in fn, "applyPreset must fetch the preset"
    assert "applyAddress(next, prev)" in fn, (
        "applyPreset must reuse applyAddress (→ switch_port), not new logic")
    assert 'api.call("switch_port"' not in fn, (
        "applyPreset must not call switch_port directly — applyAddress owns it")


def test_preset_render_escapes_user_data():
    for header in (r"function renderProfiles\s*\(",
                   r"function renderProfilePop\s*\("):
        fn = _js_block(_read(APP_JS), header)
        assert "innerHTML" in fn, f"{header} builds chips/rows"
        assert "esc_(p.name)" in fn, f"{header} must escape the preset name"
    pop = _js_block(_read(APP_JS), r"function renderProfilePop\s*\(")
    assert "esc_(p.host)" in pop and "esc_(p.port)" in pop, (
        "the preset address must be escaped too")


def test_preset_delete_calls_backend():
    fn = _js_block(_read(APP_JS), r"function renderProfilePop\s*\(")
    assert 'api.call("delete_preset"' in fn, "preset delete must call the backend"


def test_boot_loads_presets():
    fn = _js_block(_read(APP_JS), r"async function boot\s*\(")
    assert "loadPresets()" in fn, "boot must load the saved presets"


# ── backup / restore: the ownership invariant ───────────────────────────────
def test_sync_form_after_restore_never_migrates_endpoints():
    """THE invariant: restore refills the form but must not call applyAddress
    (which would migrate endpoints to the backup's address and move ownership).
    lastApplied is re-derived from the LIVE applied record, not the form."""
    fn = _js_block(_read(APP_JS), r"async function syncFormAfterRestore\s*\(")
    assert "applyAddress" not in fn, (
        "syncFormAfterRestore must not migrate endpoints on restore")
    assert "c.applied_host" in fn and "c.applied_port" in fn, (
        "lastApplied must come from the live applied record")
    assert 'api.call("get_config")' in fn, "restore must re-read the config"


def test_restore_backup_confirms_and_calls_backend():
    fn = _js_block(_read(APP_JS), r"async function restoreBackup\s*\(")
    assert "confirmDlg(" in fn, "restore is destructive to the form — confirm"
    assert 'api.call("restore_backup"' in fn, "restore must call the backend"
    assert "syncFormAfterRestore()" in fn, "restore must refill the form"


def test_backup_list_escapes_names():
    fn = _js_block(_read(APP_JS), r"async function renderBackupList\s*\(")
    assert "innerHTML" in fn
    assert "esc_(b.name)" in fn, "backup names must be escaped before innerHTML"
    assert 'api.call("list_backups")' in fn
    assert 'api.call("delete_backup"' in fn


def test_backup_button_guards_reentrancy():
    app = _read(APP_JS)
    start = app.index('$("btnBackup").onclick')
    wiring = app[start:app.index("\n};", start) + 3]
    assert 'api.call("backup_config")' in wiring
    assert "btn.disabled = true" in wiring and "btn.disabled = false" in wiring, (
        "the backup button must guard re-entrancy and recover")


def test_settings_open_renders_backup_list():
    fn = _js_block(_read(APP_JS), r"async function openSettings\s*\(")
    assert "renderBackupList()" in fn, "opening settings must show the backups"


# ── HTML elements ───────────────────────────────────────────────────────────
def test_preset_and_backup_elements_exist():
    html = _read(INDEX_HTML)
    for el_id in ("profileChips", "profilePop", "profilePopList",
                  "profileAddIn", "profileAddBtn", "backupList", "btnBackup"):
        assert f'id="{el_id}"' in html, f"index.html lacks #{el_id}"
    # The preset popup reuses the port-pop styling and starts hidden (no .show).
    m = re.search(r'<div[^>]*id="profilePop"[^>]*>', html)
    assert m and "port-pop" in m.group(0) and "show" not in m.group(0)


# ── mock bridge cases (standalone UI testing) ───────────────────────────────
def test_mock_covers_new_bridge_methods():
    app = _read(APP_JS)
    for case in ("list_presets", "get_preset", "save_preset", "delete_preset",
                 "backup_config", "list_backups", "restore_backup", "delete_backup"):
        assert f'case "{case}"' in app, f"browser mock lacks {case}"


# ── i18n: both tables, no duplicates, placeholders intact ───────────────────
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_new_keys_present_and_tables_duplicate_free(lang):
    entries = _i18n_entries(_read(I18N_JS), lang)
    counts = Counter(k for k, _ in entries)
    dupes = sorted(k for k, c in counts.items() if c > 1)
    assert not dupes, f"{lang} table has duplicate keys: {dupes}"
    table = dict(entries)
    for key in NEW_KEYS:
        assert key in table, f"{lang} table is missing {key}"
    assert "{name}" in table["tipApplyPreset"] and "{addr}" in table["tipApplyPreset"]
    assert "{name}" in table["presetSaved"]