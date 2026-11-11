"""Build/packaging guards: dependency drift, ignored secrets, version format.

`requirements.txt` is what CI and `start.bat` install; `pyproject.toml` is the
packaging metadata / `pip install .[dev]` path. If they drift, a green CI run
can ship an EXE missing a dependency (or carry a stale one). These checks keep
the two manifests in lockstep and keep sensitive artifacts out of git.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _parse_requirements(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", line)
        out[m.group(1).lower()] = m.group(2).replace(" ", "")
    return out


def _parse_pyproject_deps(items):
    out = {}
    for item in items:
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", item)
        out[m.group(1).lower()] = m.group(2).replace(" ", "")
    return out


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_runtime_requirements_match_pyproject():
    req = _parse_requirements(ROOT / "requirements.txt")
    proj = _parse_pyproject_deps(_pyproject()["project"]["dependencies"])
    assert req == proj, (
        "requirements.txt and pyproject [project].dependencies disagree:\n"
        f"  only in requirements: {sorted(req.items() - proj.items())}\n"
        f"  only in pyproject:   {sorted(proj.items() - req.items())}")


def test_build_requirements_match_pyproject_extra():
    req = _parse_requirements(ROOT / "requirements-build.txt")
    extra = _parse_pyproject_deps(_pyproject()["project"]["optional-dependencies"]["build"])
    assert req == extra, (
        "requirements-build.txt and pyproject [build] extra disagree")


def test_gitignore_covers_sensitive_and_build_artifacts():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("config.json", "config.json.bak", "config.json.corrupt",
                    "*.log", "__pycache__/", "dist/", "build/"):
        assert pattern in gi, f".gitignore no longer covers {pattern!r}"


def test_app_version_is_semver():
    import config_store
    assert re.fullmatch(r"\d+\.\d+\.\d+", config_store.APP_VERSION), (
        "APP_VERSION must be x.y.z so the update-check comparison stays sane")
