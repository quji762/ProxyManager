# AGENTS.md

本文件是 ProxyManager 的 Coding Agent 工作契约。README 面向使用者；CHANGELOG 记录重要变化；端点实现细节以 Python 代码和测试为准。

## Core invariant: never destroy unrelated proxy state

ProxyManager 会修改用户级环境变量、WinINET、WSL shell rc、Git、npm 和 pip 配置。所有写入都必须遵守：

1. 识别当前真实值；
2. 如果要覆盖一个不同的既有值，先保存它；
3. 记录本工具真正应用的值；
4. disable/quit-clear 时只移除本工具的值，或恢复先前保存值；
5. 不把“当前表单输入”误当成“已应用状态”。

任何会直接清空用户配置而不区分来源的实现都视为回归。

关闭路径的外科式守卫必须比对“本工具最后写入的 applied 地址”，而不是“当前检测值”——否则用户启用后手工改成的代理会在关闭时被误删。恢复保存值必须逐字写回原始字符串（`raw_url`），不得经 `build_proxy_url` 归一化（会降级 `https`→`http` / `socks4`→`socks5` 并丢 path）。

## Endpoint model

Windows 与 WSL 是两条独立 lane；Git/npm/pip 在两侧也是不同端点。不要为了减少 UI 项而把它们合并成一个开关。

- PowerShell：Windows 用户环境变量。
- System proxy：WinINET。
- Git/npm/pip Windows：Windows 用户配置。
- WSL terminal：受管 shell block。
- Git/npm/pip WSL：每个目标发行版内的用户配置。

开关状态应从真实系统状态回读。写入失败后 UI 必须回到实际状态，而不是保留乐观值。

## WSL invariants

- WSL2 NAT 下，Windows loopback 通常不可直接从 distro 使用；按现有逻辑解析 host gateway。
- mirrored networking 可继续使用 loopback。
- Docker/基础设施类 distro 继续跳过，不把它们当普通用户发行版。
- 设置可选择 all user distros 或 default distro；读写和恢复路径遵守相同 scope。由 all 收窄到 default 时，会按标记/记录的 URL 外科式清掉离开 scope 的发行版里本工具自己写入的配置（默认发行版不动）。
- shell block 只管理本工具自己的标记区域，禁止覆盖整个 `.bashrc`/`.zshrc`。
- `Restart WSL` 是破坏性较强的操作：会终止 distro 内进程，UI 文案/确认不能弱化。
- 短 TTL 缓存（distro 枚举、可用性探活、网络模式）只缓存成功/非空结果，绝不缓存失败或空结果（WSL 可能稍后恢复，不能被冻结为“不可用”）；`Restart WSL` 必须作废这些缓存。

## Proxy URL and secrets

- 支持 `http` / `socks5` / `socks5h`，userinfo 要正确 percent-encode。
- WinINET SOCKS 不支持用户名/密码；不要伪装成支持。
- `NO_PROXY` 合并时保留用户已有条目，并加入必要 loopback bypass。
- 密码写入本地 config 时必须经过 Windows DPAPI；DPAPI 失败要拒绝保存，不得静默写明文。
- `config.json` 和 `.bak` 仍按敏感文件处理，不应提交。
- 错误输出不得泄露凭据：子进程异常（`TimeoutExpired`/`CalledProcessError`）的 `str()` 内嵌完整 argv（含带凭据的代理 URL），必须渲染为固定本地化消息，不得直传 UI/日志/截图。
- 自定义背景图仅限本地文件：拒绝 UNC/设备路径，避免加载背景触发 SMB 认证（NTLM-relay 面）。
- 渲染 config/备份提供的动态值（端口、方案名、历史、备份列表等）必须在渲染边界 HTML 转义，杜绝注入。

## Configuration state

配置至少区分：

- 用户当前编辑的 host/port/scheme/user/password；
- `applied`：真正写到端点的最后一组值；没有 `applied` 记录表示本工具从未写入过端点，不得用表单地址回填。
- `restore`：覆盖其他代理前保存的值。

端口 quick-switch 只能重新应用“当前确实由 ProxyManager 管理并开启”的端点。

## Repository map

- `app.py`：pywebview/window/single-instance wiring。
- `js_api.py`：前端 bridge。
- `proxy_manager.py`：registry/WSL/Git/npm/pip/telemetry 系统操作。
- `config_store.py`：配置、DPAPI、autostart、备份/恢复 stash。
- `messages.py`：后端消息/i18n keys。
- `ui/`：零构建 HTML/CSS/JS UI。
- `tests/`：系统逻辑单元测试。

## Validation

```bat
pip install -r requirements.txt pytest ruff
ruff check .
python -m pytest tests/ -q
```

涉及系统写入时重点覆盖：已有不同值、重复 enable、disable、失败回滚、quit-clear、WSL NAT/mirrored、all/default distro、密码保存失败。

真实 WSL/registry 行为需要 Windows 实机验证；纯 mock 测试不能证明外部工具配置一定正确。

## Documentation rules

- README：端点范围、用户工作流、关键限制和 secret 边界。
- AGENTS.md：写入/恢复、WSL、配置状态和安全不变量。
- CHANGELOG.md：端点变化、恢复语义、安全和用户可见功能。
- 不在 README 维护完整 config key 字典；代码/默认配置才是字段真值。
