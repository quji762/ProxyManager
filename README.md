# ProxyManager

**English** | [简体中文](./README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Build & Release](https://github.com/quji762/ProxyManager/actions/workflows/release.yml/badge.svg)](https://github.com/quji762/ProxyManager/actions/workflows/release.yml)

ProxyManager is a Windows proxy switchboard for PowerShell, the Windows system proxy, WSL, Git, npm and pip. Each endpoint is managed independently, with HTTP/SOCKS support, live state, connectivity tests and safe restoration of values that existed before ProxyManager changed them.

The UI supports Chinese and English.

## Managed endpoints

Windows:

- PowerShell/user proxy environment variables
- WinINET system proxy
- Git global proxy
- npm user proxy
- pip user proxy

WSL:

- shell environment block
- Git global proxy
- npm user proxy
- pip user proxy

WSL endpoints can target all user distributions or only the default distro.

## Key features

- HTTP, SOCKS5 and SOCKS5H endpoints with optional credentials.
- Independent Win/WSL switches plus page-level enable/disable actions.
- Port presets and re-application of currently enabled endpoints.
- WSL2 NAT-aware host gateway rewriting; mirrored mode keeps loopback.
- Automatic `NO_PROXY` preservation for local traffic.
- Read-back of actual endpoint state so a failed write does not leave a misleading toggle.
- Connectivity tests through the configured proxy source.
- Restore-before-overwrite semantics: an existing different proxy is stashed and restored when ProxyManager disables its own setting.
- Tray, autostart, always-on-top, theme/background settings and optional clear-on-quit behavior.

## Requirements

- Windows 10/11
- WebView2 Runtime
- Source development: Python 3.9+
- WSL only for WSL endpoints
- Git only for Git endpoints; npm/pip config files are edited directly and do not require those CLIs

## Run from source

```bat
git clone https://github.com/quji762/ProxyManager.git
cd ProxyManager
pip install -r requirements.txt
py -3.13 app.py
```

Or run `start.bat`.

Build the single-file executable with `build.bat`; build-only dependencies are kept separately from runtime dependencies.

## Configuration and secrets

Settings are stored in `%APPDATA%\ProxyManager\config.json`. Proxy passwords are protected with Windows DPAPI; save is refused if encryption fails rather than falling back to plaintext. Treat the config and its backup as sensitive anyway.

ProxyManager tracks both the form values and the last values actually applied to endpoints. Do not collapse those concepts: typing a new host/port must not silently change what restoration logic believes was previously applied.

## Important behavior

- PowerShell environment changes affect newly started processes; already-open terminals keep their old environment.
- WSL2 NAT mode may require the Windows host gateway instead of `127.0.0.1`.
- Disabling an endpoint should remove only ProxyManager's value or restore the value it stashed before overwrite. It must not erase unrelated user configuration.
- `Restart WSL` terminates processes inside WSL.
- WinINET SOCKS settings do not support username/password.

## Development

```bat
pip install -r requirements.txt pytest ruff
ruff check .
python -m pytest tests/ -q
```

System-side write/restore rules, WSL behavior, DPAPI constraints and validation expectations are maintained in [AGENTS.md](./AGENTS.md). Important changes are recorded in [CHANGELOG.md](./CHANGELOG.md).

## License

ProxyManager is released under the [MIT License](./LICENSE).
