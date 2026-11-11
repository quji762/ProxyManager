# ProxyManager

[English](./README.md) | **简体中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Build & Release](https://github.com/quji762/ProxyManager/actions/workflows/release.yml/badge.svg)](https://github.com/quji762/ProxyManager/actions/workflows/release.yml)

ProxyManager 是一个 Windows 代理控制台，用于独立管理 PowerShell、Windows 系统代理、WSL、Git、npm 和 pip。支持 HTTP/SOCKS、实时状态、连通性测试，以及对被覆盖旧值的安全恢复。

界面支持中文和英文。

## 管理端点

Windows：

- PowerShell/用户代理环境变量
- WinINET 系统代理
- Git 全局代理
- npm 用户代理
- pip 用户代理

WSL：

- Shell 环境变量托管块
- Git 全局代理
- npm 用户代理
- pip 用户代理

WSL 可作用于全部用户发行版，或只作用于默认发行版；收窄到默认发行版时，会清掉离开 scope 的发行版里本工具自己写入的配置。

## 核心功能

- HTTP、SOCKS5、SOCKS5H，并支持可选用户名/密码。
- Win/WSL 独立开关与当前页批量启停。
- 端口预设，并自动重新应用已启用端点；部分失败时会回滚已改动的端点，并说明是哪个端点、什么原因。
- 方案预设：把整套地址（主机/端口/协议/凭据）保存为命名方案，一键在多套配置间切换。应用方案时只重新应用当前已启用且由本工具管理的端点，归属语义与换端口完全一致。
- 配置备份与一键恢复（设置页）：快照保存在受管的 `backups/` 目录。恢复只还原表单与偏好，绝不还原端点归属，因此不会让退出清理误删你自己设置的代理；恢复后请按需重新启用端点。
- WSL2 NAT 模式自动使用 Windows host gateway；mirrored mode 保持 loopback。
- 自动维护 `NO_PROXY`，让本地流量保持直连。
- 从真实系统状态回读开关，写入失败时不会留下“看起来成功”的 UI 状态。
- PAC 感知：注册了 `AutoConfigURL` 时，系统代理行下方会提示浏览器可能走 PAC 脚本，静态代理不一定生效。
- 针对具体代理来源做真实连通性测试；「全部测试」可一键并发测试当前页的所有渠道，并给出「{ok}/{total} 通过」的整体结论（超时计为失败）。最近结果只保存在内存中，可一键清空。
- 键盘无障碍：模态对话框陷阱焦点并支持 Escape 关闭，Win/WSL 标签页支持方向键切换，控件在两种语言下都有无障碍名称。
- 覆盖前保存旧值，关闭时恢复；不会把用户原有代理直接删掉。
- 托盘（显示启用端点数，可一键全部启用/全部关闭）、开机启动、置顶、主题/背景和可选退出时清理本工具代理。
- 设置中常驻版本更新状态，可重新检查，发现新版本时可一键打开下载页。
- 表单区一键复制代理地址（规范化百分号编码，含凭据）；设置页一键复制诊断摘要（版本、端点开关与环境信息，绝不含代理地址与密码）。
- 键盘快捷键：数字键 1–9 切换端口预设，Esc 关闭弹层；界面遵循 `prefers-reduced-motion` 与 Windows 高对比模式。

## 环境要求

- Windows 10/11
- WebView2 Runtime
- 源码开发：Python 3.9+
- WSL 端点才需要 WSL
- Git 端点才需要 Git；npm/pip 直接编辑配置文件，不要求对应 CLI 已安装

## 从源码运行

```bat
git clone https://github.com/quji762/ProxyManager.git
cd ProxyManager
pip install -r requirements.txt
py -3.13 app.py
```

也可以运行 `start.bat`。

使用 `build.bat` 构建单文件 EXE；构建依赖与运行时依赖分开维护。

## 配置与秘密

设置保存在 `%APPDATA%\ProxyManager\config.json`。代理密码——包括方案预设和 `%APPDATA%\ProxyManager\backups\` 下配置备份中的密码——都使用 Windows DPAPI 保护；如果加密失败则拒绝保存，而不是回退到明文。DPAPI 把密文绑定到你的 Windows 用户/机器，因此在另一台机器上恢复备份无法解密旧密码（会保留当前密码）。即使如此，配置文件、`.bak` 和 backups 目录仍应按敏感文件处理。

凭据也不会进入错误输出：子进程失败以固定的本地化消息报告，而不是原始命令行（其中可能内嵌带凭据的代理 URL）。自定义背景图仅限本地文件——拒绝 UNC 与设备路径，避免加载背景时触发 SMB 认证。

ProxyManager 同时记录“表单里正在编辑的值”和“最后真正写入端点的值”。这两个概念不能合并：仅仅输入一个新地址，不能让恢复逻辑误以为该地址已经应用。

## 重要行为

- PowerShell 用户环境变量只影响之后启动的进程；已打开终端不会自动更新。
- WSL2 NAT 模式通常需要 Windows host gateway，而不是 `127.0.0.1`。
- 关闭端点时只能移除 ProxyManager 自己写入的值，或恢复覆盖前保存的旧值；不能误删用户其他代理配置。
- “重启 WSL”会终止 WSL 内进程。
- WinINET 的 SOCKS 系统代理不支持用户名/密码。

## 开发

```bat
pip install -r requirements.txt pytest ruff
ruff check .
python -m pytest tests/ -q
```

系统写入/恢复规则、WSL 行为、DPAPI 边界和验证要求统一维护在 [AGENTS.md](./AGENTS.md)；重要变化记录在 [CHANGELOG.md](./CHANGELOG.md)。

## 许可证

ProxyManager 使用 [MIT License](./LICENSE)。
