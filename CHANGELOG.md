# Changelog

Notable endpoint, restoration, security, and compatibility changes are recorded here. Fine-grained implementation history remains in Git.

## Unreleased

### Changed
- Documentation is consolidated into README, AGENTS.md and CHANGELOG.md.
- Proxy write/restore semantics, WSL networking rules and DPAPI constraints are now explicit agent invariants.

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
