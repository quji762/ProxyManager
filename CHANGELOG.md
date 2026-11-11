# Changelog

Notable endpoint, restoration, security, and compatibility changes are recorded here. Fine-grained implementation history remains in Git.

## Unreleased

### Added
- Tray menu now shows how many endpoints are currently enabled (derived from the applied record and persisted switches, never the form fields) and adds one-click **Enable all** / **Disable all**; both run on a background worker with balloon feedback so the tray never blocks (`tray_icon.py`).
- A one-click **Test all** button probes every channel of the visible lane concurrently, painting each result as it arrives, with a 45 s watchdog and a short in-page history of recent results (kept in memory only — never written to `config.json`) (`ui/app.js`).
- The Windows lane now shows a PAC caveat under the system-proxy row when an `AutoConfigURL` is registered: browsers may follow the PAC script, so the static proxy shown is not necessarily in effect. The hint is read-only and rendered as plain text (`ui/app.js`, `js_api.py`).
- Settings gained a persistent update-status row (last check result, re-check button, and an **Open download page** button when an update exists). The download button opens only the fixed repository releases page or the cached release URL — it accepts no caller-supplied URL. The status is kept in memory only (`ui/app.js`, `js_api.py`).
- **Named address presets**: save the current form (host / port / scheme / username / password) under a name and switch between whole setups with one click. Applying a preset reuses the port-switch migration, so only the endpoints currently enabled and managed by ProxyManager are re-applied and the `applied`/`restore` ownership semantics are unchanged. Preset passwords are DPAPI-encrypted on disk; the preset list never exposes the plaintext password (`config_store.py`, `js_api.py`, `ui/app.js`).
- **Config backup and one-click restore**: snapshot the current configuration into a managed `backups/` folder (newest first, capped at 30, atomic writes) and restore it from Settings. Restore deliberately routes through `save_config`, which never accepts the ownership records (`restore`/`applied`/`wsl_written`) from caller data — so a backup brings back form fields and preferences but cannot resurrect a stale applied record or make quit-clear claim the user's current proxy. Endpoints are left as-is and re-enabled by the user. Backup names are validated and confined to the backups folder (no arbitrary path read/delete); the backed-up password stays DPAPI-encrypted (`config_store.py`, `js_api.py`, `ui/app.js`).

### Changed
- Documentation is consolidated into README, AGENTS.md and CHANGELOG.md.
- Proxy write/restore semantics, WSL networking rules and DPAPI constraints are now explicit agent invariants.
- `applied` is now only ever a real applied record: a config without it means ProxyManager has never written an endpoint, and the form address is no longer promoted into it (`config_store.py`). A plain save, a port quick-switch that migrates nothing and a legacy config therefore leave no record behind, so nothing is claimed as ours on quit-clear or quick-switch (`js_api.py`).
- PowerShell pre-enable snapshots are taken per environment variable name, covering every spelling an enable writes — both cases of `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` plus both `NO_PROXY` spellings (`js_api.py`). A restore deletes only the names enable actually wrote and no longer invents a `NO_PROXY` the user never had.
- Turning off "WSL writes to all user distros" now immediately removes this tool's shell block and Git/npm/pip entries from the distros that leave the scope; the default distro is never touched, and re-widening the scope no longer resurfaces a stale gateway address from a distro written earlier (`js_api.py`, `proxy_manager.py`).
- Port quick-switch results now surface the backend's notes (NAT gateway rewrite, PAC) and the per-endpoint failure reasons instead of a single generic line, and the "all distros" toggle reports whether the leaving-scope cleanup succeeded (`ui/app.js`).
- Performance: the `wsl -l -q` distro enumeration is cached for a couple of seconds (invalidated after `Restart WSL`), and a refresh coalesces its endpoint repaints into a single frame (`proxy_manager.py`, `ui/app.js`).

### Fixed
- Enabling an endpoint is now aborted before any write when the pre-enable stash cannot be persisted (DPAPI failure or a failed config write). Previously the failure was only logged, so the endpoint was overwritten and the user's original value was lost on exit; the error reaches the UI instead (`js_api.py`).
- Windows system proxy: a `ProxyServer` Windows leaves in the registry after the user turns the proxy off in the Internet Options UI (`ProxyEnable=0`) is now stashed and written back verbatim on disable, without re-arming the proxy. ProxyManager's own leftover is never stashed (`js_api.py`, `proxy_manager.py`).
- WSL Git/npm/pip: when a multi-distro enable fails on one distro, the distros already written are rolled back to their snapshotted user values instead of only unsetting ProxyManager's URL. A snapshot that cannot be read fails that distro rather than overwriting a proxy that was never snapshotted (`proxy_manager.py`).
- `Restart WSL` gateway rewrite checks each write's return value. A failed endpoint keeps its previous written-URL record — so quit-clear still claims it and the stale proxy does not survive in the user's distro — and the failure is reported instead of being recorded as a success (`js_api.py`).
- Port quick-switch partial failure now rolls the WSL Git endpoint back by the URL actually present in the file (the gateway form just written) instead of the typed loopback address, so a `--fixed-value` unset no longer silently no-ops and leaves the abandoned port in the distro. A rollback that itself fails is reported (which endpoint and why), and endpoints still holding the new address are listed (`js_api.py`).
- Scope-narrow cleanup now reads the distro git/npm/pip configs back after disabling: an endpoint whose recorded URL is still present — or whose distro cannot be read — keeps its written-URL record, so a later quit-clear can still claim the residue instead of orphaning it (`js_api.py`, `proxy_manager.py`).
- The multi-distro npm/pip WSL rollback also fires on a subprocess timeout: `TimeoutExpired` is not an `OSError`, and catching only `OSError` let a timed-out enable skip the rollback of the distros already written (`proxy_manager.py`).

## Current baseline

### Added
- Independent Windows and WSL proxy endpoints for shell environment, Git, npm and pip, plus Windows WinINET system proxy.
- HTTP/SOCKS5/SOCKS5H support, port presets and per-source connectivity checks.
- WSL2 NAT gateway rewriting and mirrored-network loopback handling.
- Live state synchronization, tray integration, autostart, themes and optional clear-on-quit.

### Safety
- Existing different proxy values are stashed before overwrite and restored on disable where possible.
- Endpoint switches reflect detected system state rather than assuming writes succeeded.
- Proxy passwords use Windows DPAPI and are not saved if protection fails.
- `NO_PROXY` updates preserve existing user entries.
