# ProxyManager

One-click proxy management for PowerShell, WSL, and Git on Windows: four independent switches, port presets, and live status in a Windows 11-style interface.

**English** | [简体中文](./README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Build & Release](https://github.com/quji762/ProxyManager/actions/workflows/release.yml/badge.svg)](https://github.com/quji762/ProxyManager/actions/workflows/release.yml)

## Overview

ProxyManager is a Windows desktop app that toggles HTTP proxy settings for terminal workflows. It manages four endpoints, each with its own on/off switch:

- **PowerShell** — Windows user environment variables (`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`), broadcast to the system so new terminals pick them up
- **WSL terminal** — a managed block inside `~/.bashrc`, applied to every installed user distro
- **Git · Windows** — global `git config` proxy on the Windows side
- **Git · WSL** — global `git config` proxy inside each WSL distro

The interface is a zero-build single-page web app (`ui/index.html`) rendered in WebView2 through pywebview; all system operations run in the Python backend (`proxy_manager.py`).

The tool only touches terminal-side settings (environment variables, `~/.bashrc`, `git config --global`). It never modifies the Windows system proxy.

## Features

- **Four independent switches** — enable or disable each endpoint separately; disabling removes exactly what the tool wrote and leaves manually configured values intact.
- **Switches reflect reality** — every toggle is re-synced from the actually detected proxy state, so a failed operation reverts the switch automatically.
- **Port quick-switch** — one-click switching between preset ports (7890 / 7892 / 7897 by default, fully customizable); enabled endpoints are re-applied with the new port automatically.
- **NAT-aware WSL proxy** — in WSL2 NAT mode, `127.0.0.1` is automatically replaced with the host gateway IP; mirrored mode keeps the loopback address unchanged.
- **Loopback bypass** — `NO_PROXY` is written automatically so local traffic stays direct; your existing `NO_PROXY` entries are preserved.
- **Live telemetry** — real-time network speed of the primary physical interface, proxy-port connectivity probe every second, local/WSL IP, WSL networking mode, and current proxy variables on both sides (`psutil` optional).
- **Pre-flight checks** — warns before enabling when nothing listens on the proxy port, when the WSL default shell is not bash, or when `git` is missing.
- **Windows 11-style UI** — frameless window with native edge resizing, double-click title bar to maximize, light/dark themes, custom background image with dimming and card opacity controls, always-on-top, close-to-tray, and autostart at login.
- **Under the hood** — config persistence, single-instance guard (a second launch wakes the existing window), crash logging, in-app confirm dialogs, and a one-click WSL restart that re-applies the proxy when the NAT gateway changes.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Window / runtime | Python + [pywebview](https://pypi.org/project/pywebview/) on WebView2 |
| UI | Single-file HTML/CSS/JS (`ui/index.html`), no build step |
| Tray icon | pystray + Pillow |
| Telemetry | psutil (optional) |
| Packaging | PyInstaller (`--onefile --windowed`) |
| CI / release | GitHub Actions (`.github/workflows/release.yml`) |

## Requirements

- Windows 10/11
- [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) (included with Windows 11)
- Running from source: Python 3.9+; the bundled `start.bat` and the official builds target Python 3.13
- WSL with at least one distro installed — only needed for the WSL endpoints
- Git on `PATH` — only needed for the Git endpoints

## Installation

### Packaged EXE

Download `ProxyManager.exe` from [GitHub Releases](https://github.com/quji762/ProxyManager/releases) and run it. No Python installation is required.

### From source

```bat
git clone https://github.com/quji762/ProxyManager.git
cd ProxyManager
pip install -r requirements.txt
```

`requirements.txt` includes `pyinstaller`, which is only needed for building the EXE.

## Usage

Double-click `start.bat`, or run:

```bat
py -3.13 app.py
```

`start.bat` locates a Python 3.13 interpreter automatically (Python Launcher default install path, then `pyw`/`py` on `PATH`, then the Microsoft Store alias).

Then, in the app:

1. Enter the proxy host and port (default `127.0.0.1:7897`), or click a port preset chip to switch quickly.
2. Toggle individual endpoints, or use **Enable all** / **Disable all**.
3. Theme, background image, dimming/opacity, and autostart live under **Settings** in the title bar.

Closing the window hides the app to the system tray; the tray menu restores the window or quits the app.

## Configuration

The app stores its settings in `%APPDATA%\ProxyManager\config.json` and manages the file itself — values are validated on load, so hand edits are safe but not required. Crash traces are appended to `%APPDATA%\ProxyManager\crash.log`.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | Proxy host |
| `port` | string | `7897` | Proxy port (1–65535) |
| `powershell` | bool | `true` | PowerShell endpoint switch state |
| `wsl` | bool | `true` | WSL endpoint switch state |
| `git_win` | bool | `false` | Git · Windows switch state |
| `git_wsl` | bool | `false` | Git · WSL switch state |
| `pin` | bool | `false` | Always-on-top window |
| `mode` | string | `light` | Theme: `light` or `dark` |
| `ports` | list | `[7890, 7892, 7897]` | Port presets (up to 12) |
| `autostart` | bool | `false` | Start at login (HKCU Run key) |
| `bg_path` | string | `""` | Custom background image path |
| `bg_dim` | float | `0.45` | Background dimming (0.0–0.8) |
| `card_alpha` | float | `0.92` | Card opacity (0.5–1.0) |

A legacy `git` boolean key from older versions is migrated into `git_win` / `git_wsl` automatically on load.

## Notes and Limitations

- The WSL endpoint writes to `~/.bashrc`, which only bash reads. If your default WSL shell is zsh or fish, the app warns you before writing.
- The PowerShell endpoint sets user environment variables, which apply to terminals and processes started after the change.
- Only `http://` proxy URLs are managed; the tool writes `http://host:port` exactly.
- In WSL, the managed block and git proxy are applied to every user distro; Docker infrastructure distros are skipped.
- **Restart WSL** terminates all processes running inside WSL.

## Project Structure

```text
ProxyManager/
├── app.py                     # Front-end entry: pywebview window, JS bridge, tray, Win32 window logic
├── proxy_manager.py           # Back-end library: registry / WSL / git operations, telemetry, crash log
├── ui/
│   └── index.html             # Zero-build single-page UI (HTML/CSS/JS)
├── assets/
│   ├── icon.ico               # Application / EXE icon
│   └── tray.png               # Tray icon
├── build.bat                  # Local PyInstaller build script
├── start.bat                  # Launcher: finds Python 3.13 and runs app.py
├── requirements.txt           # Runtime + build dependencies
└── .github/
    └── workflows/
        └── release.yml        # Build & Release: builds the EXE on v* tags and publishes a GitHub Release
```

## Development

Run from source with `py -3.13 app.py`. Two environment variables aid debugging:

- `PM_DEBUG=1` — verbose console output, a UI-thread heartbeat, and window-geometry probes.
- `PM_NO_SINGLETON=1` — bypass the single-instance guard so a debug instance can run next to the normal one.

The UI calls Python through the pywebview `js_api` bridge (`Api` class in `app.py`); every system-touching operation lives in `proxy_manager.py`.

## Building and Distribution

Run `build.bat` to install the build dependencies and package a single-file EXE with PyInstaller:

```bat
build.bat
```

The output is `dist\ProxyManager.exe`. The script passes `--hidden-import` for the lazily imported tray modules and bundles `ui/` and `assets/` into the executable.

The same build runs in CI: pushing a `v*` tag triggers `.github/workflows/release.yml`, which builds the EXE on Windows with Python 3.13, uploads it as an artifact, and publishes it in a GitHub Release.

## Contributing

Issues and pull requests are welcome at the [GitHub repository](https://github.com/quji762/ProxyManager). Please keep changes consistent with the existing structure: UI in `ui/index.html`, system operations in `proxy_manager.py`.

## License

This project is licensed under the MIT License — see the [LICENSE](./LICENSE) file for details.
