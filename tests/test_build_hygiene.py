"""Build/packaging guards: dependency drift, ignored secrets, version format.

`requirements.txt` is what CI and `start.bat` install; `pyproject.toml` is the
packaging metadata / `pip install .[dev]` path. If they drift, a green CI run
can ship an EXE missing a dependency (or carry a stale one). These checks keep
the two manifests in lockstep and keep sensitive artifacts out of git.
"""

import ast
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
        "APP_VERSION must be x.y.z so a release tag can match it")


def test_app_version_is_the_source_literal():
    """A stale installed proxy-manager must not override this checkout.

    setuptools and the release workflow both read the string assignment.
    Runtime has to be that same literal — importlib.metadata would return
    whatever copy happens to be installed in the environment.
    """
    import config_store
    tree = ast.parse((ROOT / "config_store.py").read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "APP_VERSION":
                assert isinstance(node.value, ast.Constant)
                assert isinstance(node.value.value, str)
                found.append(node.value.value)
    assert found == [config_store.APP_VERSION]


def test_python_floor_is_3_11_in_launcher_and_readme():
    bat = (ROOT / "start.bat").read_text(encoding="utf-8")
    loops = re.findall(r"for %%V in \(([^)]+)\)", bat)
    assert loops, "start.bat no longer has a version probe loop"
    assert all(loop.split() == ["3.13", "3.12", "3.11"] for loop in loops)
    assert 'start "" %PY_CMD% %PY_VER% "app.py"' in bat
    # The failure path must not fall through into another launch.
    # Match the label line, not the earlier `goto :deps_missing`.
    _, _, tail = bat.partition("\n:deps_missing")
    assert tail
    assert 'start ""' not in tail
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "Python 3.11+" in text
        assert "Python 3.9+" not in text


def test_release_publishes_installer_and_portable():
    """The tag workflow ships a per-user installer and a portable exe.

    dist/ProxyManager.exe is the PyInstaller input. Publishing it as well
    would put three copies of the same program on the release.
    """
    yml = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    upload = yml.split("files:", 1)[1]
    assert "dist/ProxyManager-setup.exe" in upload
    assert "dist/ProxyManager-portable.exe" in upload
    assert "dist/ProxyManager.exe" not in upload
    assert "package.bat" in yml
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss
    assert "PrivilegesRequired=admin" not in iss
    assert 'Source: "dist\\ProxyManager.exe"' in iss
    assert "CurrentVersion\\Run" not in iss
    bat = (ROOT / "package.bat").read_text(encoding="utf-8")
    assert "ISCC" in bat
    assert "ProxyManager-portable.exe" in bat
    assert "scripts\\app_version.py" in bat
