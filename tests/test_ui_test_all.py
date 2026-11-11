# -*- coding: utf-8 -*-
"""Static (no JS runtime) contract checks for the 全部测试 batch probe.

CI runs ruff + pytest only — no JS harness — so these parse ui/index.html,
ui/app.js, js_api.py and config_store.py the way tests/test_messages.py
regex-parses ui/i18n.js.

What must stay true:
  * every data-test chip in the DOM is a backend test_channel source, and
    the win/wsl split matches the two cards;
  * the batch runs its probes CONCURRENTLY (a serial sweep of 13 channels
    costs 260-400s in the worst case) with an overall watchdog;
  * each channel paints its own chip/history row as soon as IT settles;
  * the result history is memory-only — never persisted into config.json.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"


def _read(path):
    return path.read_text(encoding="utf-8")


# ── scanners ────────────────────────────────────────────────────────────────
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


def _js_callback(src, call_regex):
    """Source of the `(...)` argument list of a call, e.g. channels.map(...)."""
    m = re.search(call_regex, src)
    assert m, f"no call matching {call_regex}"
    i = src.index("(", m.start())
    depth, j, n = 0, i, len(src)
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
                return src[i:j + 1]
        j += 1
    raise AssertionError(f"unbalanced parentheses after {call_regex}")


def _html_div(src, div_id):
    """Inner HTML of `<div id="...">…</div>`, nested divs included."""
    m = re.search(rf'<div id="{div_id}"[^>]*>', src)
    assert m, f'#{div_id} not found in ui/index.html'
    i, depth, j, n = m.end(), 1, m.end(), len(src)
    while j < n and depth:
        if src.startswith("<div", j):
            depth += 1
            j += 4
        elif src.startswith("</div>", j):
            depth -= 1
            j += 6
        else:
            j += 1
    assert depth == 0, f"#{div_id} is not balanced in ui/index.html"
    return src[i:j - 6]


def _py_method(src, name):
    m = re.search(rf"^    def {name}\(", src, re.MULTILINE)
    assert m, f"js_api.py: method {name} not found"
    end = src.index("\n    def ", m.end())
    return src[m.start():end]


def _test_channels():
    """TEST_CHANNELS as declared in ui/app.js."""
    src = _read(UI_DIR / "app.js")
    out = {}
    for lane in ("win", "wsl"):
        m = re.search(rf'\b{lane}:\s*\[([^\]]*)\]', src)
        assert m, f"TEST_CHANNELS.{lane} not declared in ui/app.js"
        out[lane] = re.findall(r'"([^"]+)"', m.group(1))
    return out


def _dom_test_channels(html):
    return re.findall(r'data-test="([^"]+)"', html)


def _py_dict_region(body, dict_name):
    """Source between the braces of a `name = { … }` dict literal.

    Comment-aware (the dicts carry `#` comments with apostrophes, which a
    JS-style quote skipper would mistake for a string) and depth-counted so
    the nested `{"HTTPS_PROXY": …}` lambdas do not end the region early.
    """
    m = re.search(rf"^\s*{dict_name} = \{{", body, re.MULTILINE)
    assert m, f"test_channel has no `{dict_name}` dict"
    start = body.index("{", m.start())
    depth, j, n = 0, start, len(body)
    while j < n:
        c = body[j]
        if c == "#":
            j = body.index("\n", j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    assert depth == 0, f"unbalanced braces in `{dict_name}`"
    return body[start:j + 1]


def _backend_test_sources(js):
    """Channel keys accepted by js_api.test_channel (sources + wsl_sources)."""
    body = _py_method(js, "test_channel")
    keys = []
    for dict_name in ("sources", "wsl_sources"):
        region = _py_dict_region(body, dict_name)
        keys += re.findall(r'^\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*:',
                           region, re.MULTILINE)
    return keys


# ── DOM ↔ backend channel parity ────────────────────────────────────────────
def test_every_data_test_channel_is_a_test_channel_source():
    html = _read(UI_DIR / "index.html")
    js = _read(ROOT / "js_api.py")
    dom = set(_dom_test_channels(html))
    backend = set(_backend_test_sources(js))
    assert dom == backend, (
        f"chips in ui/index.html and test_channel sources diverge: "
        f"only-in-DOM={sorted(dom - backend)}, only-in-backend={sorted(backend - dom)}")
    assert len(dom) == 13, f"expected 13 channels, found {len(dom)}: {sorted(dom)}"


def test_channel_lists_match_dom_groups():
    html = _read(UI_DIR / "index.html")
    channels = _test_channels()
    win = _dom_test_channels(_html_div(html, "varsWin"))
    wsl = _dom_test_channels(_html_div(html, "varsWsl"))
    assert win == channels["win"], (
        f"#varsWin chips {win} != TEST_CHANNELS.win {channels['win']}")
    assert wsl == channels["wsl"], (
        f"#varsWsl chips {wsl} != TEST_CHANNELS.wsl {channels['wsl']}")
    assert len(win) == 7 and len(wsl) == 6


# ── concurrency + watchdog ──────────────────────────────────────────────────
def _arrow_body(src, header):
    """Body (braces included) of a `const name = (…) => { … }` arrow."""
    i = src.index(header)
    j = src.index("{", i)
    depth, k, n = 0, j, len(src)
    while k < n:
        c = src[k]
        if c in "\"'`":
            k = _skip_string(src, k)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1
    raise AssertionError(f"unbalanced braces in {header!r}")


def _test_all_body():
    return _js_block(_read(UI_DIR / "app.js"),
                     r"async function testAllChannels\s*\(")


def test_test_all_uses_concurrent_probes():
    body = _test_all_body()
    assert "Promise.all(" in body, "testAllChannels must join the probes with Promise.all"
    assert "channels.map(" in body, "probes must be created per channel, not awaited one by one"
    # No per-channel await inside the loop: that is the serial sweep whose
    # worst case (13 × 25-32s) is 260-400s.
    assert not re.search(r"await\s+api\.call\(\s*[\"']test_channel", body), (
        "testAllChannels awaits a probe inline — the batch would run serially")


def test_test_all_has_overall_timeout():
    body = _test_all_body()
    assert "Promise.race(" in body, "the batch needs an overall watchdog"
    assert "setTimeout(" in body, "the watchdog needs a timer"
    assert "_TEST_ALL_TIMEOUT_MS" in body, (
        "the watchdog delay must come from the named constant")
    app = _read(UI_DIR / "app.js")
    m = re.search(r"const\s+_TEST_ALL_TIMEOUT_MS\s*=\s*(\d+)", app)
    assert m, "_TEST_ALL_TIMEOUT_MS is not a named numeric constant"
    assert int(m.group(1)) >= 30000, (
        f"watchdog is {m.group(1)}ms — below the measured single-probe worst "
        f"case (WSL ~25s, Windows curl ~32s)")


def test_test_all_updates_chips_incrementally():
    body = _test_all_body()
    callback = _js_callback(body, r"channels\.map\(")
    assert "settle(" in callback, (
        "each channel's promise must record its own result when IT settles")
    funnel = _arrow_body(body, "const settle = ")
    for call in ("paintTestChip(", "pushTestHistory(", "renderHistory("):
        assert call in funnel, (
            f"the per-channel result path never reaches {call} — results "
            f"would only appear after Promise.all")
    assert body.index("settle(") < body.index("Promise.all("), (
        "results are recorded only after Promise.all — they would arrive all "
        "at once instead of incrementally")


def test_test_all_button_guards_against_double_click():
    body = _test_all_body()
    guard = body[:body.index("setBusyAll(")]
    assert "busyAll" in guard, "no busyAll early-exit guard"
    assert ("disabled" in guard) or ('classList.contains("testing")' in guard), (
        "no disabled/testing early-exit guard — a double-click would start a "
        "second batch")


# ── history stays out of the config file ────────────────────────────────────
def test_history_is_memory_only():
    app = _read(UI_DIR / "app.js")
    decl = app.index("let testHistory = []")
    window = app[decl:decl + 800]
    for banned in ("save_config", "persistSoon", "cfg."):
        assert banned not in window, (
            f"the history declaration is followed by {banned} — results would "
            f"reach config.json / the persist payload")
    assert "save_config" not in _test_all_body(), (
        "testAllChannels must not persist anything")
    assert "persistSoon" not in _test_all_body(), (
        "testAllChannels must not feed persistSoon")

    import config_store
    keys = [str(k) for k in config_store._SAVE_KEYS]
    for k in keys:
        assert "history" not in k and "test" not in k, (
            f"config_store._SAVE_KEYS persists {k!r} — test results must not "
            f"enter config.json")
