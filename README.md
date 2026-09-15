# ProxyManager

One-click proxy management for PowerShell, Windows system proxy, WSL, Git, npm, and pip on Windows: nine independent switches across Windows and WSL channel pages, port presets, and live status in a Windows 11-style interface.

Windows 代理一键管理工具：PowerShell、系统代理、WSL、两端 Git、npm 与 pip 九个端点经底部 Win / WSL 页签分页独立开关、端口快选、实时状态，Win11 风格界面。

**English** | [简体中文](./README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Build & Release](https://github.com/quji762/ProxyManager/actions/workflows/release.yml/badge.svg)](https://github.com/quji762/ProxyManager/actions/workflows/release.yml)

## Overview

ProxyManager is a Windows desktop app that toggles HTTP and SOCKS5 proxy settings for terminal workflows. The UI is Chinese/English. The status bar hosts **Win / WSL page tabs** — each page shows that system's own endpoints and variables, so every tool (Git, npm, pip) appears exactly once per page. All switches are independent:

**Windows page**

- **PowerShell** — Windows user environment variables (`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY`), broadcast to the system so new terminals pick them up
- **System proxy** — WinINET (`Internet Settings`): browsers and many GUI apps honor this
- **Git** — global `git config` proxy on the Windows side
- **npm** — `proxy` and `https-proxy` in the user-level `~/.npmrc`
- **pip** — `proxy` under `[global]` in the user-level `pip.ini` (`%APPDATA%\pip\pip.ini`), so `py -m pip` and every interpreter's pip honor it too

**WSL page** (applied to every installed user distro by default; Settings can limit this to the default distro)

- **WSL terminal** — a managed block in `~/.bashrc`, `~/.zshrc`, and fish `conf.d`
- **Git** — global `git config` proxy inside each distro
- **npm** — `proxy` / `https-proxy` in each distro's `~/.npmrc`
- **pip** — `proxy` under `[global]` in each distro's `~/.config/pip/pip.conf`; the legacy `~/.pip/pip.conf` is also cleaned on disable

The interface is a zero-build web UI (`ui/index.html` + `styles.css` / `app.js` / `i18n.js`) rendered in WebView2 through pywebview; all system operations run in the Python backend (`proxy_manager.py`).

Terminal-side endpoints write environment variables, shell rc files, `git config --global`, and npm/pip user config. The **System proxy** switch additionally writes WinINET (Windows Settings → Proxy), which is what browsers typically use.

## Features

- **HTTP and SOCKS5** — scheme is `http`, `socks5`, or `socks5h`; optional username/password is percent-encoded into the URL. Connectivity tests use `curl -x` (SOCKS included); urllib is the HTTP fallback when curl is missing.
- **Chinese / English UI** — language is a Settings option (persisted as `lang`); the native window title stays `代理管理器` so the single-instance wake path keeps working.
- **Env-var connectivity test** — every variable in the environment-variables card (HTTP_PROXY, ALL_PROXY, system proxy, Git, npm, pip; separate per page) carries a **Test** button that fires a real request THROUGH that proxy source. Several captive-portal URLs are tried in order so a blocked gstatic does not look like a dead proxy; latency is green, the failure reason is red (full text on hover).
- **Nine independent switches on Win/WSL pages** — the status-bar tabs switch the whole page between the Windows and WSL views (endpoints, network info, and env variables all follow); two vertical rails beside the endpoint list (**Enable all / Disable all**) batch-toggle the current page, and each endpoint can still be toggled separately. Disabling removes exactly what the tool wrote, or restores the value it overwrote.
- **Switches reflect reality** — every toggle is re-synced from the actually detected proxy state, so a failed operation reverts the switch automatically.
- **Port quick-switch** — one-click switching between preset ports (7890 / 7892 / 7897 by default, fully customizable); enabled endpoints are re-applied with the new port automatically.
- **NAT-aware WSL proxy** — in WSL2 NAT mode, `127.0.0.1` is automatically replaced with the host gateway IP (bashrc block and WSL npm/pip endpoints); mirrored mode keeps the loopback address unchanged.
- **Loopback bypass** — `NO_PROXY` is written automatically so local traffic stays direct; your existing `NO_PROXY` entries are preserved.
- **Live telemetry** — real-time network speed of the primary physical interface (spark-bar full scale customizable; stored as Mbps, shown as MB/s to match the download number), proxy-port connectivity probe every second, local/WSL IP, WSL networking mode, and the current lane's Git / npm / pip proxy values (`psutil` optional).
- **Pre-flight checks** — warns before enabling when nothing listens on the proxy port, when the WSL default shell is not bash/zsh/fish/sh, or when `git` is missing (npm/pip config files are written directly, so neither tool needs to be on `PATH`).
- **Windows 11-style UI** — frameless window with native edge resizing, double-click title bar to maximize, light/dark themes, custom background image with dimming and card opacity controls, always-on-top, close-to-tray (with a first-time hint), autostart at login (starts in the tray), and a setting to clear this tool's proxies on quit.
- **Under the hood** — config persistence, single-instance guard (a second launch wakes the existing window), crash logging, in-app confirm dialogs, and a one-click WSL restart that re-applies the proxy when the NAT gateway changes.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Window / runtime | Python + [pywebview](https://pypi.org/project/pywebview/) on WebView2 |
| UI | Zero-build HTML/CSS/JS (`ui/`), zh/en i18n, no bundler |
| npm/pip config | Direct INI editing of `~/.npmrc` / `pip.ini` (no CLI dependency; WSL side pulled/pushed over wsl.exe) |
| Tray icon | pystray + Pillow |
| Telemetry | psutil (optional) |
| Packaging | PyInstaller (`--onefile --windowed`) |
| CI / release | GitHub Actions (`.github/workflows/release.yml`) |

## Requirements

- Windows 10/11
- [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) (included with Windows 11)
- Running from source: Python 3.9+; `start.bat` tries 3.13 down to 3.9, then the default launcher. Official builds target Python 3.13
- WSL with at least one distro installed — only needed for the WSL endpoints
- Git on `PATH` — only needed for the Git endpoints
- npm/pip endpoints work without either tool installed: the config files are edited directly

## Installation

### Packaged EXE

Download `ProxyManager.exe` from [GitHub Releases](https://github.com/quji762/ProxyManager/releases) and run it. No Python installation is required.

### From source

```bat
git clone https://github.com/quji762/ProxyManager.git
cd ProxyManager
pip install -r requirements.txt
```

`pyinstaller` is not part of the runtime dependencies; it lives in `requirements-build.txt` (or the `[build]` extra in `pyproject.toml`) and is only needed for building the EXE.

## Usage

Double-click `start.bat`, or run:

```bat
py -3.13 app.py
```

`start.bat` locates a Python interpreter automatically (Python Launcher default install path, preferring 3.13 down to 3.9, then `pyw`/`py` on `PATH`, then the Microsoft Store alias).

Then, in the app:

1. Enter the proxy host and port (default `127.0.0.1:7897`), or click a port preset chip to switch quickly.
2. Toggle individual endpoints, or use **Enable all** / **Disable all**.
3. Theme, background image, dimming/opacity, autostart, quit-clears-proxy, and WSL distro scope live under **Settings** in the title bar.

Closing the window hides the app to the system tray (proxy stays on). The tray menu restores the window or quits the app. Editing host/port applies to already-enabled endpoints the same way a port chip does.

## Configuration

The app stores its settings in `%APPDATA%\ProxyManager\config.json` and manages the file itself — values are validated on load, so hand edits are safe but not required. Crash traces are appended to `%APPDATA%\ProxyManager\crash.log`.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | Proxy host |
| `port` | string | `7897` | Proxy port (1–65535) |
| `scheme` | string | `http` | `http`, `socks5`, or `socks5h` |
| `user` | string | `""` | Optional proxy username (no `:` `@` or whitespace) |
| `password` | string | `""` | Optional proxy password (DPAPI-encrypted in this config file; falls back to plaintext if DPAPI fails) |
| `lang` | string | `zh` | UI language: `zh` or `en` |
| `powershell` | bool | `true` | PowerShell endpoint switch state |
| `wsl` | bool | `true` | WSL endpoint switch state |
| `git_win` | bool | `false` | Git · Windows switch state |
| `git_wsl` | bool | `false` | Git · WSL switch state |
| `npm` | bool | `false` | npm (Windows) switch state |
| `pip` | bool | `false` | pip (Windows) switch state |
| `npmwsl` | bool | `false` | npm (WSL) switch state |
| `pipwsl` | bool | `false` | pip (WSL) switch state |
| `pin` | bool | `false` | Always-on-top window |
| `mode` | string | `light` | Theme: `light` or `dark` |
| `ports` | list | `[7890, 7892, 7897]` | Port presets (up to 12) |
| `autostart` | bool | `false` | Start at login (HKCU Run key) |
| `bg_path` | string | `""` | Custom background image path |
| `bg_dim` | float | `0.45` | Background dimming (0.0–0.8) |
| `card_alpha` | float | `0.92` | Card opacity (0.5–1.0) |
| `speed_full` | float | `40` | Spark-bar full-scale speed in Mbps (0.1–100000); the UI shows MB/s |
| `quit_clear` | bool | `false` | On tray Quit, disable this tool's proxies and restore overwritten values |
| `wsl_all_distros` | bool | `true` | Write WSL endpoints to every user distro (`false` = default distro only) |
| `close_hint_done` | bool | `false` | First-close “hides to tray” hint has been shown |
| `restore` | object | `{}` | Stashed pre-overwrite proxy URLs, keyed by endpoint |
| `applied` | object | form fields | Last host/port/scheme/user/password actually written to endpoints; kept separate from the form fields so a typed-but-not-yet-applied address cannot poison port switching after a restart |
| `win_rect` | list | `null` | Last normal window bounds (physical pixels); restored on launch |
| `win_max` | bool | `false` | Start with the window maximized |

A legacy `git` boolean key from older versions is migrated into `git_win` / `git_wsl` automatically on load.

## Notes and Limitations

- The WSL terminal endpoint writes `~/.bashrc`, `~/.zshrc`, and fish `~/.config/fish/conf.d/proxy-manager.fish`. Other shells are warned before writing.
- The PowerShell endpoint sets user environment variables, which apply to terminals and processes started after the change. Already-open windows keep their old environment until you open a new one.
- Proxy URLs are `http://`, `socks5://`, or `socks5h://`, with optional userinfo. WinINET **system proxy** SOCKS writes `socks=host:port` and **does not support username/password**.
- The password is stored in `%APPDATA%\ProxyManager\config.json` (local user file), encrypted with Windows DPAPI bound to the current user. Treat the file as a secret anyway; DPAPI cannot be decrypted after copying to another machine or Windows account. If DPAPI fails, the value is stored in plaintext and a line is written to the crash log. Do not copy that file into a shared repo. A successful save also writes `config.json.bak`; a corrupt primary file is recovered from that backup (including the restore stash) and the UI shows a toast.
- In WSL, the managed block and git/npm/pip proxies are applied to every user distro by default (Settings can limit this to the default distro); Docker infrastructure distros are skipped.
- Enabling an endpoint that already had a different proxy stashes that value and restores it on disable (or on quit, when **quit-clears-proxy** is on). Switches are on only for this tool's URL (including the WSL NAT gateway rewrite); a proxy the user configured for another endpoint shows off, so clicking it stashes and overwrites instead of deleting.
- The WSL npm/pip endpoints read back and modify config files over `wsl.exe`; every read/write round-trip costs a wsl.exe call, so the WSL switches refresh a bit slower than the Windows ones.
- **Restart WSL** terminates all processes running inside WSL.

## Project Structure

```text
ProxyManager/
├── app.py                     # Entry: pywebview window, singleton, wiring
├── js_api.py                  # JS bridge (Api class)
├── config_store.py            # config.json load/save, autostart, version
├── window_win32.py            # Frameless resize, caption drag, HWND helpers
├── tray_icon.py               # Tray icon + Win11 promotion
├── proxy_manager.py           # Registry / WSL / git / npm / pip / telemetry
├── ui/
│   ├── index.html             # Markup
│   ├── styles.css             # Theme tokens + layout
│   ├── i18n.js                # zh / en strings
│   └── app.js                 # UI logic (browser mock included)
├── tests/                     # pytest unit tests (no WSL required)
├── assets/
│   ├── icon.ico
│   └── tray.png
├── build.bat                  # PyInstaller (installs requirements-build.txt)
├── start.bat
├── pyproject.toml             # Version, runtime deps, ruff/pytest config
├── requirements.txt           # Runtime dependencies (same pins as pyproject)
├── requirements-build.txt     # Runtime + PyInstaller
└── .github/workflows/         # ci.yml (ruff + pytest) and release.yml
```

## Development

Run from source with `py -3.13 app.py`. Two environment variables aid debugging:

- `PM_DEBUG=1` — verbose console output, a UI-thread heartbeat, and window-geometry probes.
- `PM_NO_SINGLETON=1` — bypass the single-instance guard so a debug instance can run next to the normal one.

The UI calls Python through the pywebview `js_api` bridge (`Api` in `js_api.py`); every system-touching operation lives in `proxy_manager.py`.

```bat
pip install -r requirements.txt pytest ruff
ruff check .
python -m pytest tests/ -q
```

## Building and Distribution

Run `build.bat` to install the build dependencies and package a single-file EXE with PyInstaller:

```bat
build.bat
```

The output is `dist\ProxyManager.exe`. The script passes `--hidden-import` for the lazily imported tray modules and bundles `ui/` and `assets/` into the executable.

The same build runs in CI: pushing a `v*` tag triggers `.github/workflows/release.yml`, which builds the EXE on Windows with Python 3.13, uploads it as an artifact, and publishes it in a GitHub Release.

## Contributing

Issues and pull requests are welcome at the [GitHub repository](https://github.com/quji762/ProxyManager). Please keep changes consistent with the existing structure: UI in `ui/`, window/tray/bridge in their modules, system operations in `proxy_manager.py`. Add tests next to the helpers they cover.

## License

This project is licensed under the MIT License — see the [LICENSE](./LICENSE) file for details.
