"""Print config_store.APP_VERSION without importing the module.

The release workflow and package.bat both need the literal. Importing
config_store loads WinDLL entry points and can pick up a stale installed
distribution instead of this checkout.
"""

import ast
import sys
from pathlib import Path


def app_version(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if (isinstance(target, ast.Name) and target.id == "APP_VERSION"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                return node.value.value
    return None


def main():
    root = Path(__file__).resolve().parent.parent
    version = app_version(root / "config_store.py")
    if not version:
        print("APP_VERSION string literal not found in config_store.py",
              file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
