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

WSL endpoints can target all user distributions or only the default distro; narrowing to the default distro removes ProxyManager's own settings from the distros that leave the scope.

## Key features

- HTTP, SOCKS5 and SOCKS5H endpoints with optional credentials.
- Independent Win/WSL switches plus page-level enable/disable actions.
- Port presets and re-application of currently enabled endpoints; a partial failure rolls back what was changed and reports which endpoint failed and why.
- Named address presets: save a whole address (host/port/scheme/credentials) under a name and switch between setups in one click. Applying a preset re-applies only the endpoints currently enabled and managed by ProxyManager, with the same ownership-safe migration as a port switch.
- Config backup and one-click restore from Settings: snapshots live in a managed `backups/` folder. Restore brings back form fields and preferences only — it never restores endpoint ownership, so it cannot make quit-clear claim a proxy you set yourself; re-enable endpoints after restoring.
- WSL2 NAT-aware host gateway rewriting; mirrored mode keeps loopback.
- Automatic `NO_PROXY` preservation for local traffic.
- Read-back of actual endpoint state so a failed write does not leave a misleading toggle.
- PAC awareness: when an `AutoConfigURL` is registered, a hint under the system-proxy row warns that browsers may follow the PAC script instead of the static proxy.
- Connectivity tests through the configured proxy source, plus a one-click **Test all** that probes every channel of the current page at once and reports an overall `{ok}/{total} passed` verdict (counting timeouts as failures). Recent results are kept in memory only and can be cleared with one click.
- Keyboard-accessible UI: modal dialogs trap focus and close on Escape, the Win/WSL tabs support arrow-key navigation, and controls carry accessible names in both languages.
- Restore-before-overwrite semantics: an existing different proxy is stashed and restored when ProxyManager disables its own setting.
- Tray with a live enabled-endpoint count and one-click enable/disable all, autostart, always-on-top, theme/background settings and optional clear-on-quit behavior.
- Update check with a persistent status row in Settings, a re-check button and a one-click link to the releases download page.
- One-click **Copy URL** from the proxy form (canonical percent-encoding, credentials included) and **Copy diagnostics** in Settings (version, endpoint switches and environment summary — never the proxy address or password).
- Keyboard shortcuts: digits **1–9** switch port presets and **Esc** closes popovers; the UI honours `prefers-reduced-motion` and Windows High Contrast.

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

Settings are stored in `%APPDATA%\ProxyManager\config.json`. Proxy passwords — including those saved in named presets and in config backups under `%APPDATA%\ProxyManager\backups\` — are protected with Windows DPAPI; save is refused if encryption fails rather than falling back to plaintext. DPAPI ties the ciphertext to your Windows user/machine, so a backup restored on another machine cannot decrypt the old password (the previous password is kept). Treat the config, its `.bak` and the backups folder as sensitive anyway.

Credentials are also kept out of error output: subprocess failures are reported as fixed localized messages rather than the raw command line (which can embed a credentialed proxy URL). Custom background images are restricted to local files — UNC and device paths are rejected so loading a background cannot trigger SMB authentication.

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
