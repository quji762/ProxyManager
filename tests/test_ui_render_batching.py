# -*- coding: utf-8 -*-
"""Static (no JS runtime) checks for the refresh render batching.

CI runs ruff + pytest only — no JS harness — so ui/app.js is parsed with
brace/quote-aware scanners, like tests/test_messages.py parses ui/i18n.js.

Contract: refreshAll's eight parallel probe callbacks each used to repaint
the whole endpoint list; they now go through one 30ms debounced
scheduleRender(), while the synchronous first frame at the end of refreshAll
(and every user-action call site) keeps calling renderEndpoints() directly.
"""

import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


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


def _balanced_parens(src, open_idx):
    """Source from `src[open_idx]` (a `(`) through its matching `)`."""
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


def _js_callbacks(src, call_regex):
    """Source of every `call(...)` argument list matching call_regex."""
    out = []
    for m in re.finditer(call_regex, src):
        out.append(_balanced_parens(src, src.index("(", m.start())))
    return out


def _refresh_all():
    return _js_block(_read(UI_DIR / "app.js"), r"async function refreshAll\s*\(")


def _schedule_render():
    return _js_block(_read(UI_DIR / "app.js"), r"function scheduleRender\s*\(")


# ── batching inside refreshAll ──────────────────────────────────────────────
def test_refresh_all_batches_endpoint_renders():
    """Eight endpoint probe callbacks + one synchronous first frame.

    The `ip` / `mode` probes also use .then() but only fill in a value — they
    never repainted the list and must stay that way. Every callback that
    renders must go through the debounce; a bare renderEndpoints() left in a
    probe callback would repaint the whole list once per settling probe.
    """
    body = _refresh_all()
    callbacks = _js_callbacks(body, r"\.then\(")
    assert len(callbacks) >= 8, (
        f"refreshAll has only {len(callbacks)} probe callbacks — the "
        f"parallel probe layout changed; re-check this contract")
    batched, probes = [], set()
    for cb in callbacks:
        assert "renderEndpoints()" not in cb, (
            f"a probe callback still repaints directly: {cb[:120]!r}")
        if "scheduleRender()" in cb:
            batched.append(cb)
    assert len(batched) == 8, (
        f"expected 8 batched probe callbacks, found {len(batched)}")
    # Which probes batch? The eight endpoint probes — the ip/mode value
    # probes render nothing and must stay that way.
    for m in re.finditer(
            r'api\.call\(\s*"refresh_item",\s*"([a-z_]+)"\s*\)\s*\.then\(',
            body):
        cb = _balanced_parens(body, m.end() - 1)
        if "scheduleRender()" in cb:
            probes.add(m.group(1))
    assert probes == {"ps", "sys", "wsl", "gitwin", "npm", "pip",
                      "npmwsl", "pipwsl"}, (
        f"batched probes are {sorted(probes)} — one endpoint probe is "
        f"missing its scheduleRender()")
    # Exactly one bare renderEndpoints() remains: the synchronous first frame
    # at the very end of refreshAll (no empty window before probes settle).
    assert body.count("renderEndpoints();") == 1, (
        "refreshAll must keep exactly one direct renderEndpoints() — the "
        "synchronous first frame")
    assert body.rstrip().endswith("renderEndpoints();\n}"), (
        "the remaining renderEndpoints() is not the final frame of refreshAll")


def test_schedule_render_is_debounced():
    fn = _schedule_render()
    assert re.search(r"if\s*\(\s*renderTimer\s*\)\s*return", fn), (
        "scheduleRender must early-exit while a frame is already pending")
    assert "setTimeout(" in fn, "scheduleRender must defer via setTimeout"
    m = re.search(r"setTimeout\([^,]*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", fn)
    assert m and m.group(1) == "_RENDER_BATCH_MS", (
        "the delay must come from the named _RENDER_BATCH_MS constant")
    app = _read(UI_DIR / "app.js")
    v = re.search(r"const\s+_RENDER_BATCH_MS\s*=\s*(\d+)", app)
    assert v, "_RENDER_BATCH_MS is not declared"
    assert int(v.group(1)) > 0, "a zero debounce would not batch anything"


def test_schedule_render_keeps_single_frame_semantics():
    """The timer must call the one real renderer — no second implementation
    that could drift from renderEndpoints()."""
    fn = _schedule_render()
    timer = fn[fn.index("setTimeout("):]
    assert "renderEndpoints()" in timer, (
        "the debounce callback must render through renderEndpoints()")
    assert "innerHTML" not in timer and "epCard" not in timer, (
        "the debounce callback must not reimplement rendering")


def test_other_render_call_sites_unchanged():
    """User-action renders stay synchronous: a debounce there would lag the
    click, and these are single renders anyway."""
    app = _read(UI_DIR / "app.js")
    for header, why in ((r"function setBusy\s*\(", "setBusy"),
                        (r"async function toggleEndpoint\s*\(", "toggleEndpoint"),
                        (r"function setLane\s*\(", "setLane")):
        body = _js_block(app, header)
        assert "renderEndpoints()" in body, f"{why} no longer renders endpoints"
        assert "scheduleRender" not in body, (
            f"{why} must render synchronously, not through the debounce")
