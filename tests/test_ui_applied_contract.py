"""Static contract check: boot() must not fabricate an applied record.

Contract A2 (AGENTS.md): no applied record means the tool never wrote an
endpoint, so the form address must NOT be promoted to the UI's lastApplied.
boot() used `c.applied_host || c.host` which fabricated ownership of a proxy
the user configured by hand at the same address — clicking that switch OFF
would then delete it. syncFormAfterRestore already uses the correct `|| ""`
form; boot() must match it.

The repo has no JS runtime harness (CI runs ruff + pytest), so this parses
ui/app.js the same way tests/test_ui_feedback.py does.
"""

import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "ui" / "app.js"


def _boot_last_applied_block():
    src = APP_JS.read_text(encoding="utf-8")
    boot = src.index("async function boot()")
    # The first lastApplied assignment inside boot() is the config bootstrap.
    start = src.index("lastApplied = {", boot)
    end = src.index("};", start)
    return src[start:end]


def test_boot_does_not_promote_form_address_to_applied():
    block = _boot_last_applied_block()
    # host/port must come from the applied record only, never the form fields.
    assert "c.applied_host || c.host" not in block
    assert "c.applied_port || c.port" not in block
    assert re.search(r"host:\s*String\(c\.applied_host\s*\|\|\s*\"\"\)", block)
    assert re.search(r"port:\s*String\(c\.applied_port\s*\|\|\s*\"\"\)", block)


def test_boot_applied_scheme_not_fallen_back_to_form():
    block = _boot_last_applied_block()
    assert "c.applied_scheme || c.scheme" not in block
    assert re.search(r"scheme:\s*c\.applied_scheme\s*\|\|\s*\"http\"", block)


def test_boot_matches_sync_form_after_restore():
    """Both bootstrap paths must derive lastApplied identically (A2)."""
    src = APP_JS.read_text(encoding="utf-8")
    sync = src.index("async function syncFormAfterRestore()")
    s_start = src.index("lastApplied = {", sync)
    s_block = src[s_start:src.index("};", s_start)]
    boot_block = _boot_last_applied_block()
    for field in ("c.applied_host", "c.applied_port", "c.applied_scheme"):
        assert field in boot_block and field in s_block
    assert "c.host" not in boot_block and "c.port" not in boot_block