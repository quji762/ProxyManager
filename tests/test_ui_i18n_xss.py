"""Static (no JS runtime) contract checks for R7: i18n completeness + XSS.

The repo has no JS test harness — CI runs ruff + pytest only — so these parse
ui/app.js, ui/i18n.js and ui/index.html with brace/quote-aware scanners, the
same way tests/test_messages.py and tests/test_ui_feedback.py do.

Scope:
  * port presets (cfg.ports) come from config.json / restored backups and can
    hold arbitrary strings, so renderPorts/renderPortPop must HTML-escape the
    value in every attribute and text slot (the other renderers already do);
  * a language switch must repaint the generated history rows and the backup
    list's action buttons, which are not data-i18n nodes;
  * the zh/en tables stay in lockstep and every statically-referenced key
    (t("…"), data-i18n*, EP_DEFS nameKey/descKey) exists in both.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
APP_JS = (UI / "app.js").read_text(encoding="utf-8")
I18N_JS = (UI / "i18n.js").read_text(encoding="utf-8")
HTML = (UI / "index.html").read_text(encoding="utf-8")


# ── scanners ────────────────────────────────────────────────────────────────
def _func_body(src, name):
    """Source of `function name(...) { ... }` via a quote-aware brace scan."""
    start = src.index(f"function {name}(")
    brace = src.index("{", start)
    i = brace + 1
    depth = 1
    n = len(src)
    while i < n and depth:
        c = src[i]
        if c in "\"'`":
            quote = c
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    break
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[brace:i]


def _i18n_tables():
    tables = {}
    for lang in ("zh", "en"):
        m = re.search(rf"\b{lang}:\s*\{{(.*?)(?=^\s*\}},)", I18N_JS,
                      re.MULTILINE | re.DOTALL)
        assert m, f"{lang} table not found"
        keys = re.findall(r'^\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_.]*))\s*:',
                          m.group(1), re.MULTILINE)
        tables[lang] = {a or b for a, b in keys}
    return tables


# ── XSS: port presets must be escaped ───────────────────────────────────────
def test_render_ports_escapes_port_value():
    body = _func_body(APP_JS, "renderPorts")
    assert 'data-port="${esc_(p)}"' in body
    assert '>${esc_(p)}</button>' in body
    # No raw interpolation of the config-supplied port may remain.
    assert not re.search(r"\$\{p\}", body), "unescaped ${p} in renderPorts"


def test_render_port_pop_escapes_port_value():
    body = _func_body(APP_JS, "renderPortPop")
    assert 'data-del="${esc_(p)}"' in body
    assert '<span class="pv">${esc_(p)}' in body
    assert not re.search(r"\$\{p\}", body), "unescaped ${p} in renderPortPop"


def test_other_renderers_keep_escaping_dynamic_data():
    # Regression guard: preset names / backup names / history errors stay
    # escaped (these were already correct; keep them that way).
    assert 'data-preset="${esc_(p.name)}"' in _func_body(APP_JS, "renderProfiles")
    assert 'data-restore="${esc_(b.name)}"' in _func_body(APP_JS, "renderBackupList")
    assert "esc_(h.error" in _func_body(APP_JS, "renderHistory")


# ── language switch repaints generated content ──────────────────────────────
def test_apply_i18n_repaints_history_and_backups():
    body = _func_body(I18N_JS, "applyI18n")
    assert "renderHistory()" in body
    assert "renderBackupList()" in body
    # Backups only re-fetch while settings is open (no boot-time round-trip).
    assert 'settingsOv' in body and 'classList.contains("show")' in body


def test_apply_i18n_repaints_pac_hint_and_speed_cap():
    body = _func_body(I18N_JS, "applyI18n")
    assert 'typeof renderPacHint === "function"' in body
    assert 'typeof renderSpeedCap === "function"' in body
    assert "renderPacHint();" in body
    assert "renderSpeedCap();" in body


def test_esc_covers_all_html_metacharacters():
    line = next(ln for ln in APP_JS.splitlines() if ln.startswith("function esc_"))
    assert '[&<>"\']' in line
    for ent in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert ent in line


def test_history_render_escapes_dynamic_fields():
    start = APP_JS.index("function renderHistory()")
    block = APP_JS[start:APP_JS.index("async function testAllChannels", start)]
    assert "esc_(h.error" in block
    assert "esc_(testChannelName(" in block


def test_endpoint_card_escapes_detected_value():
    start = APP_JS.index("function epCard(")
    block = APP_JS[start:APP_JS.index("function renderEndpoints", start)]
    assert "esc_(val)" in block


def test_test_chip_and_history_localize_backend_error():
    # test_channel returns an error_key; show the localized text, not r.error.
    assert "const et = msgText(r || {})" in APP_JS
    assert "settle(which, !!(r && r.ok), r && r.ms, msgText(r || {})" in APP_JS


def test_autostart_failure_uses_msg_text():
    assert 't("setFail", { err: msgText(r || {})' in APP_JS


def test_copy_proxy_url_wired_and_localized():
    assert 'id="btnCopyProxy"' in HTML
    assert 'api.call("get_proxy_url"' in APP_JS
    assert 'case "get_proxy_url"' in APP_JS          # mock parity
    for key in ("copyProxy", "tipCopyProxy", "copiedUrl"):
        assert key + ":" in I18N_JS


def test_diagnostics_button_wired_and_localized():
    assert 'id="btnCopyDiag"' in HTML
    assert 'api.call("get_diagnostics"' in APP_JS
    assert 'case "get_diagnostics"' in APP_JS        # mock parity
    for key in ("diagTitle", "diagDesc", "diagCopy", "diagCopied"):
        assert key + ":" in I18N_JS


# ── i18n completeness ───────────────────────────────────────────────────────
def test_zh_en_tables_have_identical_keys():
    tables = _i18n_tables()
    assert tables["zh"] == tables["en"], (
        f"zh-only={sorted(tables['zh'] - tables['en'])} "
        f"en-only={sorted(tables['en'] - tables['zh'])}")


def test_every_static_key_exists_in_both_tables():
    tables = _i18n_tables()
    used = set(re.findall(r'\bt\(\s*["\']([^"\']+)["\']', APP_JS))
    used |= set(re.findall(r'\bt\(\s*["\']([^"\']+)["\']', I18N_JS))
    used |= set(re.findall(r'data-i18n(?:-tip|-placeholder|-aria)?=["\']([^"\']+)["\']',
                           HTML))
    used |= set(re.findall(r'data-i18n(?:-tip|-placeholder|-aria)?=["\']([^"\']+)["\']',
                           APP_JS))
    used |= set(re.findall(r'(?:nameKey|descKey)\s*:\s*["\']([^"\']+)["\']', APP_JS))
    for key in sorted(used):
        assert key in tables["zh"], f"{key} missing from zh table"
        assert key in tables["en"], f"{key} missing from en table"