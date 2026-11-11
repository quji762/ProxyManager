"""Static (no JS runtime) contract checks for the port-switch feedback paths.

The repo has no JS test harness — CI runs ruff + pytest only — so these
checks parse ui/app.js, ui/i18n.js and js_api.py with brace/quote-aware
scanners, the same way tests/test_messages.py regex-parses ui/i18n.js.

Scope (B1 of the UI feedback work):
  * the port-switch success toast must render the backend's NAT/PAC note
    instead of the generic line;
  * the port-switch failure toast must keep the per-endpoint detail that
    msgText() alone would drop when error_key is set;
  * errSwitchPartial must not claim a restore that may not have happened;
  * the "all distros" toggle must surface the scope-cleanup result.

The success/failure checks are deliberately DOUBLE-SIDED: they also assert
the producer half in js_api.py (_switch_port returning note_parts,
save_config returning wsl_cleanup at the wsl_all_distros transition), so
the UI cannot look green while the backend half of the contract is missing.
"""

import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
APP_JS = UI_DIR / "app.js"
I18N_JS = UI_DIR / "i18n.js"
JS_API = ROOT / "js_api.py"


def _read(path):
    return path.read_text(encoding="utf-8")


# ── scanners ────────────────────────────────────────────────────────────────
def _skip_string(src, i):
    """Index just past the string literal starting at src[i] (quote char)."""
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


def _i18n_entries(src, lang):
    """Ordered [(key, value)] of one language table, duplicates preserved.

    Brace-, quote- and comment-aware: values contain {placeholders}, Chinese
    quotes and em dashes, and the table bodies carry /* … */ comments.
    """
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


def _table(src, lang):
    return dict(_i18n_entries(src, lang))


def _js_block(src, header_regex):
    """Body (braces included) of the first JS function matching header_regex."""
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


def _js_handler(src, assignee):
    """Body of an `X.onclick = … { … };` wiring statement."""
    i = src.index(assignee)
    end = src.index("\n};", i) + 3
    return src[i:end]


def _py_method(src, name):
    """Source of one `def name(` method, up to the next method at its level."""
    m = re.search(rf"^    def {name}\(", src, re.MULTILINE)
    assert m, f"js_api.py: method {name} not found"
    end = src.index("\n    def ", m.end())
    return src[m.start():end]


# ── i18n table hygiene ──────────────────────────────────────────────────────
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_i18n_tables_have_no_duplicate_keys(lang):
    """A key defined twice is a silent override: the surviving definition
    lost its {addr}/{n} interpolation params last time (zh:179 / en:403
    overrode the parameterised `switched`)."""
    entries = _i18n_entries(_read(I18N_JS), lang)
    counts = Counter(k for k, _ in entries)
    for key in ("switched", "switchedPartial", "errSwitchPartial",
                "wslScopeCleaned"):
        assert counts[key] == 1, (
            f"{lang}.{key} is defined {counts[key]} times — the later "
            f"definition overrides the earlier one")
    table = dict(entries)
    assert "{addr}" in table["switched"], f"{lang}.switched lost {{addr}}"
    assert "{n}" in table["switched"], f"{lang}.switched lost {{n}}"


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_err_switch_partial_text_does_not_claim_full_restore(lang):
    """errSwitchPartial must hold when the rollback was partial or itself
    failed — no unconditional "restored the previous address" claim."""
    banned = {"zh": ("已恢复到原来的地址",),
              "en": ("restored the previous address",)}[lang]
    text = _table(_read(I18N_JS), lang)["errSwitchPartial"]
    for phrase in banned:
        assert phrase not in text, (
            f"{lang}.errSwitchPartial still claims a full restore: {text!r}")


# ── port switch: success path (double-sided) ────────────────────────────────
def test_switch_port_success_path_renders_backend_note():
    """UI half: applyAddress's success branch must render the backend note
    (NAT gateway rewrite / PAC) instead of only the generic line.
    Backend half: _switch_port must actually send note_parts."""
    app = _read(APP_JS)
    apply_addr = _js_block(app, r"async function applyAddress\s*\(")
    success = apply_addr[apply_addr.index("} else {"):]
    assert "r.note" in success and "r.note_parts" in success, (
        "applyAddress's success branch ignores the backend note")
    assert "msgText(r)" in success, (
        "applyAddress's success branch does not render the note via msgText")

    js = _read(JS_API)
    switch = _py_method(js, "_switch_port")
    m = re.search(r'return \{\s*"ok": True,\s*"applied": applied,.*?\}',
                  switch, re.DOTALL)
    assert m, "js_api._switch_port has no `ok: True` success return"
    assert "note_parts" in m.group(0), (
        "js_api._switch_port's success return carries no note_parts — "
        "the NAT/PAC notes never reach the UI")


# ── port switch: failure path keeps the per-endpoint detail ─────────────────
def test_switch_port_failure_path_keeps_detail():
    app = _read(APP_JS)
    fn = _js_block(app, r"function switchErrText\s*\(")
    assert "t(" in fn, "switchErrText must localize the error_key"
    assert "r.error" in fn, (
        "switchErrText must keep the backend detail (r.error / msgText(r))")
    assert "return t(r.error_key)" not in fn, (
        "switchErrText must not collapse to the bare localized key — that is "
        "what swallowed the per-endpoint failure reasons")

    apply_addr = _js_block(app, r"async function applyAddress\s*\(")
    failure = apply_addr[apply_addr.index("if (r && r.error)"):
                        apply_addr.index("} else {")]
    assert "switchErrText(r)" in failure, (
        "applyAddress's failure branch must toast switchErrText(r)")


# ── "all distros" toggle surfaces the cleanup result (double-sided) ─────────
def test_wsl_all_toggle_surfaces_cleanup_result():
    app = _read(APP_JS)
    handler = _js_handler(app, '$("tglWslAll").onclick')
    assert "wsl_cleanup" in handler, (
        "tglWslAll ignores the wsl_cleanup result of save_config")
    assert "wslScopeCleaned" in handler, (
        "tglWslAll never toasts the wslScopeCleaned message")

    js = _read(JS_API)
    save = _py_method(js, "save_config")
    transition = save.index('"wsl_all_distros" in data')
    assert "wsl_cleanup" in save[transition:], (
        "js_api.save_config does not report wsl_cleanup at the "
        "wsl_all_distros transition — the UI has nothing to surface")
