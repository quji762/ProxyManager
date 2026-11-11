"""Guard: every subprocess call in the shipped modules sets a timeout.

An unbounded ``subprocess.run`` can hang a UI worker or the quit path forever
when ``wsl.exe`` / ``git`` wedges (the call has no deadline). All current
call sites already pass ``timeout=``; this test freezes that invariant so a
new call that forgets it (or passes ``timeout=None``) fails in CI instead of
turning into an unrecoverable hang on a user's machine.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(ROOT.glob("*.py"))
FUNCS = {"run", "Popen", "check_output", "call", "check_call"}


def _unbounded_calls(path):
    """Line numbers of subprocess.<fn> calls with no timeout or timeout=None."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "subprocess"
                and fn.attr in FUNCS):
            continue
        timeout = next((k for k in node.keywords if k.arg == "timeout"), None)
        unbounded = timeout is None or (
            isinstance(timeout.value, ast.Constant) and timeout.value.value is None)
        if unbounded:
            bad.append(node.lineno)
    return bad


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_every_subprocess_call_has_timeout(path):
    bad = _unbounded_calls(path)
    assert bad == [], \
        f"{path.name}: subprocess calls without a real timeout at lines {bad}"


def test_guard_actually_detects_a_missing_timeout(tmp_path):
    """Meta-check: the scanner must not silently pass everything."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import subprocess\n"
        "subprocess.run(['x'], capture_output=True)\n"
        "subprocess.run(['y'], timeout=None)\n"
        "subprocess.run(['z'], timeout=5)\n",
        encoding="utf-8")
    assert _unbounded_calls(sample) == [2, 3]
