# -*- coding: utf-8 -*-
"""Static (no JS runtime) contract checks for the PAC hint and the
persistent update-status row.

CI runs ruff + pytest only — no JS harness — so ui/app.js / ui/i18n.js /
ui/index.html are parsed with brace/quote-aware scanners, like
tests/test_ui_feedback.py and tests/test_ui_render_batching.py.

Contracts:
  * the pac probe is VALUE-ONLY: it fills the hint under the WinINET row
    and must never join the 8-probe render batch (that count is pinned by
    tests/test_ui_render_batching.py);
  * the PAC URL is registry data and reaches the DOM via textContent only;
  * the update status lives in memory (never config.json) and the download
    button calls open_release_page with NO url argument;
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

NEW_KEYS = ("updateTitle", "updateUnknown", "updateChecking", "updateLatest",
            "updateAvailStatus", "updateError", "updateRecheck",
            "updateDownload", "pacActive")


def _read(path):
    return path.read_text(encoding="utf-8")


# ── scanners (same shape as tests/test_ui_render_batching.py) ───────────────
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


def _balanced_parens(src, open_idx):
    depth, j, n = 0, open_idx, len(src)
    while j < n:
        c = src[j]
        if c in "\"'`":
            j = _skip_string(src, j)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[open_idx:j + 1]
        j += 1
    raise AssertionError("unbalanced parentheses")


def _i18n_entries(src, lang):
    """Ordered [(key, value)] of one language table, duplicates preserved."""
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


# ── PAC probe: value-only, win lane only ────────────────────────────────────
def test_pac_probe_is_value_only():
    """The pac probe fills a hint, not the endpoint list. A scheduleRender()
    or renderEndpoints() in its callback would break the pinned 8-probe
    batch in tests/test_ui_render_batching.py."""
    app = _read(APP_JS)
    body = _js_block(app, r"async function refreshAll\s*\(")
    m = re.search(r'api\.call\(\s*"refresh_item",\s*"pac"\s*\)\s*\.then\(',
                  body)
    assert m, "refreshAll no longer probes refresh_item('pac')"
    cb = _balanced_parens(body, m.end() - 1)
    assert "scheduleRender" not in cb and "renderEndpoints" not in cb, (
        f"the pac probe repaints the endpoint list: {cb[:120]!r}")
    assert "renderPacHint()" in cb, "the pac probe never renders the hint"
    # win lane only — the WSL card has no WinINET row
    line_start = body.rfind("\n", 0, m.start()) + 1
    assert 'if (lane === "win")' in body[line_start:m.start()], (
        "the pac probe must be guarded to the win lane")


def test_pac_hint_element_inside_vars_win():
    html = _read(INDEX_HTML)
    m = re.search(r'<div[^>]*id="pacHint"[^>]*>', html)
    assert m, "index.html has no #pacHint element"
    tag = m.group(0)
    assert "hidden" in tag, "#pacHint must start hidden"
    assert "net-hint" in tag and "pac-hint" in tag, (
        "#pacHint must carry the net-hint pac-hint classes")
    win = html.index('<div id="varsWin">')
    wsl = html.index('<div id="varsWsl"')
    assert win < m.start() < wsl, (
        "#pacHint must live inside #varsWin so the WSL lane hides it "
        "with the whole card")
    sys_row = html.index('id="vSys"')
    git_row = html.index('id="vPsGit"')
    assert sys_row < m.start() < git_row, (
        "#pacHint must sit between the WinINET row and the Git row")


def test_render_pac_hint_uses_text_content_only():
    """The PAC URL is registry data — untrusted HTML must never be
    injected through it."""
    fn = _js_block(_read(APP_JS), r"function renderPacHint\s*\(")
    assert "textContent" in fn, "renderPacHint must write via textContent"
    assert "innerHTML" not in fn, "renderPacHint must not use innerHTML"
    assert 't("pacActive"' in fn, "renderPacHint must localize via pacActive"
    assert "el.hidden = true" in fn, (
        "renderPacHint must hide the hint when no PAC is registered")


# ── update row: elements, memory-only state, no-url download ────────────────
def test_update_row_elements_exist():
    html = _read(INDEX_HTML)
    for el_id in ("updateStatus", "btnUpdateCheck", "btnUpdateDl"):
        assert f'id="{el_id}"' in html, f"index.html lacks #{el_id}"
    m = re.search(r'<button[^>]*id="btnUpdateDl"[^>]*>', html)
    assert m and "hidden" in m.group(0), (
        "the download button must start hidden (only shown on 'update')")


def test_update_status_is_memory_only():
    """updateInfo feeds a status row; persisting it would put a transient
    network result into config.json."""
    app = _read(APP_JS)
    start = app.index("let updateChecked")
    end = app.index("async function doUpdateCheck")
    end = app.index("\n}", end) + 2
    region = app[start:end]
    for banned in ("save_config", "persistSoon", "cfg."):
        assert banned not in region, (
            f"the update-status code persists state via {banned}")
    for header in (r"function checkUpdateOnce\s*\(",
                   r"function renderUpdateStatus\s*\(",
                   r"async function doUpdateCheck\s*\("):
        fn = _js_block(app, header)
        for banned in ("save_config", "persistSoon", "cfg."):
            assert banned not in fn, f"{header} persists state via {banned}"


def test_update_status_maps_every_backend_status():
    fn = _js_block(_read(APP_JS), r"function renderUpdateStatus\s*\(")
    for key in ("updateUnknown", "updateLatest", "updateAvailStatus",
                "updateError"):
        assert f't("{key}"' in fn, f"renderUpdateStatus never shows {key}"
    assert 'r.status === "update"' in fn, (
        "the download button visibility must hinge on status === 'update'")


def test_do_update_check_guards_and_recovers():
    fn = _js_block(_read(APP_JS), r"async function doUpdateCheck\s*\(")
    assert "btn.disabled" in fn, "doUpdateCheck must guard re-entrancy"
    assert 't("updateChecking")' in fn, "no busy feedback while checking"
    assert "catch" in fn, "a bridge failure must not leave the row stuck"
    assert fn.count("btn.disabled = false") >= 1, (
        "the button must be re-enabled after the check")


def test_download_button_calls_open_release_page_without_url():
    app = _read(APP_JS)
    m = re.search(r'\$\("btnUpdateDl"\)\.onclick\s*=[^;]*;', app)
    assert m, "btnUpdateDl is not wired"
    wiring = m.group(0)
    assert 'api.call("open_release_page")' in wiring, (
        "btnUpdateDl must call open_release_page")
    assert 'api.call("open_release_page",' not in wiring, (
        "btnUpdateDl must not pass a URL — the backend takes none")


def test_settings_open_and_lang_change_rerender_status():
    app = _read(APP_JS)
    settings = _js_block(app, r"async function openSettings\s*\(")
    assert "renderUpdateStatus()" in settings, (
        "opening settings must show the current update status")
    lang = app[app.index('$("inLang").addEventListener'):]
    lang = lang[:lang.index("\n});") + 4]
    assert "renderUpdateStatus()" in lang and "renderPacHint()" in lang, (
        "a language switch must re-render both dynamic texts")


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
    assert "{url}" in table["pacActive"], f"{lang}.pacActive lost {{url}}"
    assert "{latest}" in table["updateAvailStatus"], (
        f"{lang}.updateAvailStatus lost {{latest}}")