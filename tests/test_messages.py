"""The backend message catalog (messages.TEMPLATES) and the frontend i18n
table (ui/i18n.js) must stay in sync: every key the backend can emit must
exist in BOTH language tables, or an English UI would fall back to the zh
text (t() returns the key itself when missing — visible gibberish).

The JS table is parsed with a small brace-aware scanner instead of a JS
engine: the file is a flat `key: "value"` list inside two language blocks.
"""

import re
from pathlib import Path

import messages

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def _load_i18n_tables():
    src = (UI_DIR / "i18n.js").read_text(encoding="utf-8")
    tables = {}
    for lang in ("zh", "en"):
        m = re.search(rf"\b{lang}:\s*\{{(.*?)(?=^\s*\}},)", src,
                      re.MULTILINE | re.DOTALL)
        assert m, f"{lang} table not found in ui/i18n.js"
        body = m.group(1)
        keys = set(re.findall(r'^\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_.]*))\s*:', body, re.MULTILINE))
        tables[lang] = {a or b for a, b in keys}
    return tables


def test_backend_keys_exist_in_both_i18n_tables():
    tables = _load_i18n_tables()
    for key in messages.TEMPLATES:
        assert key in tables["zh"], f"{key} missing from ui/i18n.js zh table"
        assert key in tables["en"], f"{key} missing from ui/i18n.js en table"


def test_backend_templates_render_with_params():
    """Every template must survive .format() with its own placeholders —
    a typo'd placeholder name would raise KeyError at runtime, on the
    user-facing error path of all places."""
    placeholder = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
    for key, tpl in messages.TEMPLATES.items():
        for lang, text in tpl.items():
            names = set(placeholder.findall(text))
            s = text.format(**dict.fromkeys(names, "x"))
            assert s, f"{key}/{lang} rendered empty"


def test_lstr_zh_value_and_key_params():
    s = messages.LStr("errWslWriteDetail", {"where": "WSL(Ubuntu): ",
                                            "detail": "disk full"})
    # str value is the zh rendering (logs / legacy callers / tests)
    assert str(s) == "WSL(Ubuntu): 写入 WSL 代理配置失败：disk full"
    assert s.key == "errWslWriteDetail"
    assert s.params["detail"] == "disk full"
    parts = messages.parts_of(s)
    assert parts == [{"key": "errWslWriteDetail",
                      "params": {"where": "WSL(Ubuntu): ", "detail": "disk full"}}]


def test_ljoin_keeps_parts_addressable():
    a = messages.LStr("errNoGitWslWhere", {"where": "WSL(Ubuntu): "})
    b = messages.LStr("errNoGitWslWhere", {"where": ""})
    joined = messages.LJoin([a, b, "plain"])
    assert str(joined) == "WSL(Ubuntu): 未找到 git; 未找到 git; plain"
    parts = messages.parts_of(joined)
    assert parts and parts[0]["key"] == "errNoGitWslWhere"
    assert parts[1] == {"key": "errNoGitWslWhere", "params": {"where": ""}}
    # The plain string contributes no {key,...} part; parts_of drops it.
    assert len(parts) == 2


def test_parts_of_plain_string_is_none():
    assert messages.parts_of("oops") is None
    assert messages.part_fields("oops") is None


def test_escaped_braces_render_literally_after_format():
    # {{b}} is a format escape: .format() folds it to {b}. LStr must not
    # raise on such a template.
    messages.TEMPLATES["_t"] = {"zh": "a {{b}} c", "en": "a {{b}} c"}
    try:
        s = messages.LStr("_t")
        assert str(s) == "a {b} c"
        assert s.key == "_t"
    finally:
        del messages.TEMPLATES["_t"]


# ── js_api._endpoint_err: the bridge-side error_parts contract ────────────────
from js_api import _endpoint_err  # noqa: E402


def test_endpoint_err_plain_string_gets_label():
    out = _endpoint_err("ps", "disk full")
    assert out["error"] == "PowerShell: disk full"
    assert "error_parts" not in out  # plain strings carry no i18n parts


def test_endpoint_err_sys_label_bilingual():
    out = _endpoint_err("sys", "access denied")
    assert out["error"] == "系统代理: access denied"
    # 纯字符串详情也要作为 text 部件下发：只发 label 会让前端 msgText 因
    # parts 非空提前返回，真正的失败原因被吞（契约C1）。
    assert out["error_parts"] == [
        {"key": "labelSys"}, {"text": "access denied"}]


def test_endpoint_err_lstr_passes_parts_through():
    from messages import LStr
    detail = LStr("errNoGitWslWhere", {"where": "WSL(Ubuntu): "})
    out = _endpoint_err("gitwsl", detail)
    assert out["error"] == "WSL(Ubuntu): 未找到 git"
    assert out["error_parts"] == [{
        "key": "errNoGitWslWhere", "params": {"where": "WSL(Ubuntu): "}}]


def test_endpoint_err_ljoin_passes_all_parts():
    from messages import LJoin, LStr
    detail = LJoin([
        LStr("errNoGitWslWhere", {"where": "WSL(Ubuntu): "}),
        LStr("errNoGitWslWhere", {"where": ""}),
    ])
    out = _endpoint_err("gitwsl", detail)
    assert len(out["error_parts"]) == 2
    assert out["error_parts"][0]["params"]["where"] == "WSL(Ubuntu): "
