# ProxyManager

One-click proxy management for PowerShell, Windows system proxy, WSL, Git, npm, and pip on Windows: nine independent switches across Windows and WSL channel pages, port presets, and live status in a Windows 11-style interface.

Windows 代理一键管理工具：PowerShell、系统代理、WSL、两端 Git、npm 与 pip 九个端点经底部 Win / WSL 页签分页独立开关、端口快选、实时状态，Win11 风格界面。

[English](./README.md) | **简体中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Build & Release](https://github.com/quji762/ProxyManager/actions/workflows/release.yml/badge.svg)](https://github.com/quji762/ProxyManager/actions/workflows/release.yml)

## 项目简介

ProxyManager 是一个面向终端场景的 Windows 桌面代理管理工具，支持 HTTP 与 SOCKS5，界面中英双语。底部状态栏提供 **Win / WSL 页签**——每个页面只显示该系统的端点与代理变量，Git、npm、pip 等软件每页只出现一次。所有开关相互独立：

**Windows 页**

- **PowerShell** — 写入 Windows 用户环境变量（`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY`），并向系统广播变更，新开的终端即可生效
- **系统代理** — WinINET（「Internet 选项」），浏览器和不少图形程序会走它
- **Git** — Windows 端 `git config --global` 代理
- **npm** — 用户级 `~/.npmrc` 中的 `proxy` 与 `https-proxy`
- **pip** — 用户级 `pip.ini`（`%APPDATA%\pip\pip.ini`）中 `[global]` 段的 `proxy`，`py -m pip` 与所有解释器的 pip 均会读取

**WSL 页**（默认应用于所有已安装的用户发行版；设置里可改为只写默认发行版）

- **WSL 终端** — 写入 `~/.bashrc`、`~/.zshrc` 与 fish `conf.d` 中的受管代码块
- **Git** — 各发行版内 `git config --global` 代理
- **npm** — 各发行版 `~/.npmrc` 中的 `proxy` / `https-proxy`
- **pip** — 各发行版 `~/.config/pip/pip.conf` 中 `[global]` 段的 `proxy`；禁用时会一并清理旧版 `~/.pip/pip.conf`

界面是一个零构建的 Web UI（`ui/index.html` + `styles.css` / `app.js` / `i18n.js`），经 pywebview 渲染在 WebView2 中；所有涉及系统的操作都在 Python 后端（`proxy_manager.py`）完成。

终端侧端点改的是环境变量、shell rc、`git config --global` 和 npm/pip 用户级配置。「系统代理」开关会额外写入 WinINET（Windows 设置 → 代理），也就是浏览器通常读取的那一项。

## 功能特性

- **HTTP / SOCKS5** — 协议可选 `http`、`socks5`、`socks5h`，用户名密码写入 URL（百分号编码）。连通性测试走 `curl -x`（含 SOCKS）；没有 curl 时 HTTP 回退到 urllib。
- **中英界面** — 设置里切换语言（写入 `lang`），后端提示（操作结果、错误原因）同样按界面语言显示；原生窗口标题仍是「代理管理器」，以免单实例唤醒找不到窗口。
- **更新检查** — 启动后向 GitHub Releases 查询一次新版本（5 秒超时、静默失败，离线无感知，不做后台轮询）；有新版本时提示一次。
- **环境变量测试** — 「环境变量」卡片中每个变量（HTTP_PROXY、ALL_PROXY、系统代理、Git、npm、pip，Windows / WSL 两页各自独立）带「测试」按钮，点击后经对应代理源发起真实连通性请求。会按顺序尝试多个探测地址，避免 gstatic 被墙时误报红；绿色显示延迟，红色显示失败原因（完整原文在悬停提示里）。
- **九端点 Win/WSL 分页** — 底部状态栏页签在 Windows / WSL 两个页面间切换整页内容（端点、网络信息、环境变量随之联动）；端点列表右侧两条纵向开关（全部开启 / 全部关闭）批量操作当前页，各端点亦可单独启停。关闭时只移除本工具写入的配置，或恢复覆盖前的值。
- **状态即真相** — 每次操作后开关都会按实际检测到的代理状态重新同步，操作失败时开关自动弹回。
- **端口快选** — 预设端口一键切换（默认 7890 / 7892 / 7897，可自定义增删）；切换时已启用的端点自动以新端口重新应用。
- **NAT 自适应** — WSL2 NAT 模式下自动将 `127.0.0.1` 替换为宿主网关 IP（bashrc 受管块与 WSL 侧 npm/pip 端点）；镜像模式保持回环地址不变。
- **回环直连** — 自动写入 `NO_PROXY`，本地回环流量不走代理；用户已有的 `NO_PROXY` 条目会被保留。
- **实时遥测** — 主物理网卡实时网速（进度条满格值可自定义；内部按 Mbps 存储，界面按 MB/s 显示，与下载数字单位一致）、代理端口连通性每秒探测、本机/WSL IP、WSL 网络模式、当前渠道的 Git / npm / pip 代理值（`psutil` 为可选依赖）。
- **启用前检查** — 代理端口无监听、WSL 默认 shell 不是 bash/zsh/fish/sh、未找到 `git` 时，启用前都会先行提示（npm/pip 直接编辑配置文件，两个工具无需在 PATH 中）。
- **Win11 风格界面** — 无边框窗口、边缘原生缩放、双击标题栏最大化、亮/暗主题、自定义背景图与遮罩/卡片不透明度调节、窗口置顶、关闭到系统托盘（首次关闭会提示）、开机自启（登录后在托盘运行）、退出时可选择关闭本工具写入的代理。
- **基础能力** — 配置持久化、单实例守卫（重复启动会唤醒已有窗口）、崩溃日志、应用内确认对话框、一键重启 WSL（网关变化时自动重新应用代理）。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 窗口 / 运行时 | Python + [pywebview](https://pypi.org/project/pywebview/)（WebView2） |
| 界面 | 零构建 HTML/CSS/JS（`ui/`），中英 i18n，无打包器 |
| npm/pip 配置 | 直接行级编辑 `~/.npmrc` / `pip.ini`（不依赖 CLI；WSL 侧经 wsl.exe 拉取/回写） |
| 系统托盘 | pystray + Pillow |
| 遥测 | psutil（可选） |
| 打包 | PyInstaller（`--onefile --windowed`） |
| CI / 发布 | GitHub Actions（`.github/workflows/release.yml`） |

## 环境要求

- Windows 10/11
- [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Windows 11 自带）
- 源码运行：Python 3.9+；`start.bat` 会从 3.13 试到 3.9，再退回启动器默认。官方构建以 Python 3.13 为目标
- WSL 功能需已安装 WSL 且至少有一个发行版
- Git 端点需 `git` 在 `PATH` 中
- npm/pip 端点无需两个工具已安装：配置文件由本工具直接编辑

## 安装

### 打包 EXE

从 [GitHub Releases](https://github.com/quji762/ProxyManager/releases) 下载 `ProxyManager.exe` 直接运行，无需安装 Python。

### 源码运行

```bat
git clone https://github.com/quji762/ProxyManager.git
cd ProxyManager
pip install -r requirements.txt
```

`pyinstaller` 不在运行时依赖里，位于 `requirements-build.txt`（或 `pyproject.toml` 的 `[build]` extra），仅在打包 EXE 时需要。

## 使用方法

双击 `start.bat`，或执行：

```bat
py -3.13 app.py
```

`start.bat` 会自动查找 Python 解释器（依次尝试 Python Launcher 默认安装路径，优先 3.13 到 3.9、`PATH` 中的 `pyw`/`py`、Microsoft Store 别名）。

启动后：

1. 填写代理地址与端口（默认 `127.0.0.1:7897`），或点击端口预设快速切换。
2. 打开对应端点的开关；「全部开启 / 全部关闭」可一键操作当前页全部端点。
3. 主题、背景图、遮罩/不透明度、开机自启、退出时关闭代理、WSL 发行版范围在标题栏「设置」中。

关闭窗口会隐藏到系统托盘（代理保持生效）。托盘菜单可恢复窗口或退出程序。直接改地址/端口输入框，会像点端口芯片一样重新应用到已启用的端点。

## 配置

应用将设置保存在 `%APPDATA%\ProxyManager\config.json`，由程序自行维护；加载时会校验各字段，手工编辑不会导致出错，但通常没有必要。崩溃信息追加写入 `%APPDATA%\ProxyManager\crash.log`。

| 键 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | 代理地址 |
| `port` | string | `7897` | 代理端口（1–65535） |
| `scheme` | string | `http` | `http` / `socks5` / `socks5h` |
| `user` | string | `""` | 可选用户名（不含 `:`、`@`、空白） |
| `password` | string | `""` | 可选密码（本机配置文件中经 DPAPI 加密；DPAPI 失败时拒绝保存，不会明文落盘） |
| `lang` | string | `zh` | 界面语言：`zh` 或 `en` |
| `powershell` | bool | `true` | PowerShell 端点开关状态 |
| `wsl` | bool | `true` | WSL 端点开关状态 |
| `git_win` | bool | `false` | Git · Windows 开关状态 |
| `git_wsl` | bool | `false` | Git · WSL 开关状态 |
| `npm` | bool | `false` | npm（Windows）开关状态 |
| `pip` | bool | `false` | pip（Windows）开关状态 |
| `npmwsl` | bool | `false` | npm（WSL）开关状态 |
| `pipwsl` | bool | `false` | pip（WSL）开关状态 |
| `pin` | bool | `false` | 窗口置顶 |
| `mode` | string | `light` | 主题：`light` 或 `dark` |
| `ports` | list | `[7890, 7892, 7897]` | 端口预设（最多 12 个） |
| `autostart` | bool | `false` | 开机自启（写入 HKCU Run 键） |
| `bg_path` | string | `""` | 自定义背景图片路径 |
| `bg_dim` | float | `0.45` | 背景遮罩强度（0.0–0.8） |
| `card_alpha` | float | `0.92` | 卡片不透明度（0.5–1.0） |
| `speed_full` | float | `40` | 实时网速进度条满格值（Mbps，0.1–100000；界面按 MB/s 显示） |
| `quit_clear` | bool | `false` | 托盘「退出」时关闭本工具写入的代理，并恢复覆盖前的值 |
| `wsl_all_distros` | bool | `true` | WSL 端点写入所有用户发行版（`false` 则只写默认发行版） |
| `close_hint_done` | bool | `false` | 是否已显示过首次关闭「隐藏到托盘」提示 |
| `restore` | object | `{}` | 覆盖前暂存的代理 URL，按端点名索引 |
| `applied` | object | 与表单字段相同 | 实际写入各端点的 host/port/scheme/user/password；与表单字段分开保存，避免“已输入但尚未应用到端点”的地址在重启后污染端口切换 |
| `win_rect` | list | `null` | 上次正常状态的窗口位置（物理像素）；启动时恢复 |
| `win_max` | bool | `false` | 启动时窗口最大化 |

旧版本的 `git` 布尔键会在加载时自动迁移为 `git_win` / `git_wsl`。

## 注意事项与已知限制

- WSL 终端端点会写入 `~/.bashrc`、`~/.zshrc` 和 fish 的 `~/.config/fish/conf.d/proxy-manager.fish`。其他 shell 会在写入前提示。
- PowerShell 端点写的是用户环境变量，对变更之后新开的终端和进程生效。已经打开的窗口仍是旧环境，需要新开一个。
- 代理 URL 为 `http://`、`socks5://` 或 `socks5h://`，可带用户名密码。系统代理的 SOCKS 写入 `socks=host:port`，**不支持用户名密码**。
- 密码保存在 `%APPDATA%\ProxyManager\config.json`（本机用户文件），用当前 Windows 用户的 DPAPI 加密。换机或换账户无法解密；DPAPI 失败时拒绝保存并提示，不会明文落盘。不要把该文件提交到仓库。每次成功保存会另写 `config.json.bak`；主文件损坏时从备份恢复（含覆盖前暂存），并弹出提示。
- WSL 侧的受管代码块与 git/npm/pip 代理默认应用到所有用户发行版（设置里可改为只写默认发行版）；Docker 基础设施发行版会被跳过。
- 启用时如果该端点已有别的代理，会先暂存再覆盖；关闭（或勾选「退出时关闭代理」后从托盘退出）会把暂存值写回去。开关仅在检测到本工具写入的 URL 时为开（含 WSL NAT 网关改写）；用户自己配的其它代理显示为关，点击是「暂存并覆盖」而不是直接删除。
- WSL 侧 npm/pip 端点经 `wsl.exe` 读写配置文件，每次读取/写入都是一次 wsl.exe 往返，因此 WSL 开关的状态刷新比 Windows 侧慢。
- 「重启 WSL」会终止 WSL 内正在运行的所有进程。
- 打包 EXE 未做代码签名：首次运行可能触发 SmartScreen 提示，点「仍要运行」即可。每个正式 Release 都附带 `SHA256SUMS.txt`，下载后请以其校验和为准。

## 项目结构

```text
ProxyManager/
├── app.py                     # 入口：pywebview 窗口、单实例、WebView2 检测、装配
├── js_api.py                  # JS 桥（Api）
├── messages.py                # 后端提示双语目录（zh 文案 + i18n key）
├── config_store.py            # config.json、开机自启、版本号（唯一来源）
├── window_win32.py            # 无边框缩放、标题栏拖动、HWND
├── tray_icon.py               # 托盘与 Win11 提升
├── proxy_manager.py           # 注册表 / WSL / git / npm / pip / 遥测
├── ui/                        # index.html + styles.css + i18n.js + app.js
├── tests/                     # pytest（不需要 WSL）
├── assets/
├── build.bat                  # 安装 requirements-build.txt 后打包
├── start.bat
├── pyproject.toml             # 版本号（动态读取 config_store.APP_VERSION）、依赖、ruff/pytest 配置
├── requirements.txt           # 运行时依赖（与 pyproject 同一组 pin）
├── requirements-build.txt     # 运行时 + PyInstaller
└── .github/workflows/         # ci.yml 与 release.yml
```

## 开发

源码运行：`py -3.13 app.py`。两个环境变量可辅助调试：

- `PM_DEBUG=1` — 输出详细日志、UI 线程心跳与窗口几何探测信息。
- `PM_NO_SINGLETON=1` — 跳过单实例守卫，便于调试实例与正式实例并行运行。

界面通过 pywebview 的 `js_api` 桥调用 Python（`js_api.py` 中的 `Api`）；所有涉及系统的操作都收敛在 `proxy_manager.py` 中。

```bat
pip install -r requirements.txt pytest ruff
ruff check .
python -m pytest tests/ -q
```

## 构建分发

运行 `build.bat`，脚本会安装构建依赖并用 PyInstaller 打包单文件 EXE：

```bat
build.bat
```

产物为 `dist\ProxyManager.exe`。脚本通过 `--hidden-import` 带上延迟导入的托盘相关模块，并把 `ui/` 与 `assets/` 一并打入可执行文件。

CI 执行相同的构建：推送 `v*` tag 会触发 `.github/workflows/release.yml`，先校验 tag 与 `config_store.APP_VERSION` 一致，再在 Windows 上使用 Python 3.13 构建 EXE，连同 `SHA256SUMS.txt` 校验和直接发布到 GitHub Release。

## 参与贡献

欢迎在 [GitHub 仓库](https://github.com/quji762/ProxyManager) 提交 Issue 与 Pull Request。请保持现有分层：界面放 `ui/`，窗口/托盘/桥放对应模块，系统操作放 `proxy_manager.py`。纯逻辑改动请补测试。

## 许可证

本项目基于 MIT 许可证开源，详见 [LICENSE](./LICENSE)。
