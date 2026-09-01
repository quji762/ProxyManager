# ProxyManager

Windows 代理一键管理工具：PowerShell、WSL、两端 Git、npm 与 pip 八个端点经底部 Win / WSL 页签分页独立开关、端口快选、实时状态，Win11 风格界面。

[English](./README.md) | **简体中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Build & Release](https://github.com/quji762/ProxyManager/actions/workflows/release.yml/badge.svg)](https://github.com/quji762/ProxyManager/actions/workflows/release.yml)

## 项目简介

ProxyManager 是一个面向终端场景的 Windows 桌面代理管理工具。底部状态栏提供 **Win / WSL 页签**——每个页面只显示该系统的端点与代理变量，Git、npm、pip 等软件每页只出现一次。所有开关相互独立：

**Windows 页**

- **PowerShell** — 写入 Windows 用户环境变量（`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`），并向系统广播变更，新开的终端即可生效
- **Git** — Windows 端 `git config --global` 代理
- **npm** — 用户级 `~/.npmrc` 中的 `proxy` 与 `https-proxy`
- **pip** — 用户级 `pip.ini`（`%APPDATA%\pip\pip.ini`）中 `[global]` 段的 `proxy`，`py -m pip` 与所有解释器的 pip 均会读取

**WSL 页**（应用于所有已安装的用户发行版）

- **WSL 终端** — 写入 `~/.bashrc` 中的受管代码块
- **Git** — 各发行版内 `git config --global` 代理
- **npm** — 各发行版 `~/.npmrc` 中的 `proxy` / `https-proxy`
- **pip** — 各发行版 `~/.config/pip/pip.conf` 中 `[global]` 段的 `proxy`；禁用时会一并清理旧版 `~/.pip/pip.conf`

界面是一个零构建的单页 Web 应用（`ui/index.html`），经 pywebview 渲染在 WebView2 中；所有涉及系统的操作都在 Python 后端（`proxy_manager.py`）完成。

本工具只修改终端侧的代理配置（环境变量、`~/.bashrc`、`git config --global`、npm/pip 用户级配置文件），不会改动 Windows 系统代理。

## 功能特性

- **环境变量测试** — 「环境变量」卡片中每个变量（HTTP_PROXY、Git、npm、pip，Windows / WSL 两页各自独立）带「测试」按钮，点击后经对应代理源发起真实连通性请求（Windows 走 urllib、WSL 内走 curl，目标 gstatic generate_204），绿色显示延迟、红色显示失败原因。
- **八端点 Win/WSL 分页** — 底部状态栏页签在 Windows / WSL 两个页面间切换整页内容（端点、网络信息、环境变量随之联动），为后续新渠道预留空间；端点列表右侧两条纵向开关（全部开启 / 全部关闭）批量操作当前页，各端点亦可单独启停。关闭时只移除本工具写入的配置，手动设置的值不受影响。
- **状态即真相** — 每次操作后开关都会按实际检测到的代理状态重新同步，操作失败时开关自动弹回。
- **端口快选** — 预设端口一键切换（默认 7890 / 7892 / 7897，可自定义增删）；切换时已启用的端点自动以新端口重新应用。
- **NAT 自适应** — WSL2 NAT 模式下自动将 `127.0.0.1` 替换为宿主网关 IP（bashrc 受管块与 WSL 侧 npm/pip 端点）；镜像模式保持回环地址不变。
- **回环直连** — 自动写入 `NO_PROXY`，本地回环流量不走代理；用户已有的 `NO_PROXY` 条目会被保留。
- **实时遥测** — 主物理网卡实时网速（进度条满格值可自定义，默认 5 Mbps）、代理端口连通性每秒探测、本机/WSL IP、WSL 网络模式、当前渠道的 Git / npm / pip 代理值（`psutil` 为可选依赖）。
- **启用前检查** — 代理端口无监听、WSL 默认 shell 不是 bash、未找到 `git` 时，启用前都会先行提示（npm/pip 直接编辑配置文件，两个工具无需在 PATH 中）。
- **Win11 风格界面** — 无边框窗口、边缘原生缩放、双击标题栏最大化、亮/暗主题、自定义背景图与遮罩/卡片不透明度调节、窗口置顶、关闭到系统托盘、开机自启。
- **基础能力** — 配置持久化、单实例守卫（重复启动会唤醒已有窗口）、崩溃日志、应用内确认对话框、一键重启 WSL（网关变化时自动重新应用代理）。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 窗口 / 运行时 | Python + [pywebview](https://pypi.org/project/pywebview/)（WebView2） |
| 界面 | 单文件 HTML/CSS/JS（`ui/index.html`），无构建步骤 |
| npm/pip 配置 | 直接行级编辑 `~/.npmrc` / `pip.ini`（不依赖 CLI；WSL 侧经 wsl.exe 拉取/回写） |
| 系统托盘 | pystray + Pillow |
| 遥测 | psutil（可选） |
| 打包 | PyInstaller（`--onefile --windowed`） |
| CI / 发布 | GitHub Actions（`.github/workflows/release.yml`） |

## 环境要求

- Windows 10/11
- [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Windows 11 自带）
- 源码运行：Python 3.9+；随附的 `start.bat` 与官方构建以 Python 3.13 为目标
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

`requirements.txt` 中的 `pyinstaller` 仅在打包 EXE 时需要。

## 使用方法

双击 `start.bat`，或执行：

```bat
py -3.13 app.py
```

`start.bat` 会自动查找 Python 3.13 解释器（依次尝试 Python Launcher 默认安装路径、`PATH` 中的 `pyw`/`py`、Microsoft Store 别名）。

启动后：

1. 填写代理地址与端口（默认 `127.0.0.1:7897`），或点击端口预设快速切换。
2. 打开对应端点的开关；「全部启用 / 全部关闭」可一键操作全部端点。
3. 主题、背景图、遮罩/不透明度、开机自启在标题栏「设置」中。

关闭窗口会隐藏到系统托盘，托盘菜单可恢复窗口或退出程序。

## 配置

应用将设置保存在 `%APPDATA%\ProxyManager\config.json`，由程序自行维护；加载时会校验各字段，手工编辑不会导致出错，但通常没有必要。崩溃信息追加写入 `%APPDATA%\ProxyManager\crash.log`。

| 键 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | 代理地址 |
| `port` | string | `7897` | 代理端口（1–65535） |
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
| `speed_full` | float | `5` | 实时网速进度条满格值（Mbps，0.1–100000） |

旧版本的 `git` 布尔键会在加载时自动迁移为 `git_win` / `git_wsl`。

## 注意事项与已知限制

- WSL 端点写入的是 `~/.bashrc`，只有 bash 会读取。若 WSL 默认 shell 是 zsh 或 fish，应用会在写入前提示。
- PowerShell 端点写的是用户环境变量，对变更之后新开的终端和进程生效。
- 仅管理 `http://` 形式的代理，写入内容固定为 `http://host:port`。
- WSL 侧的受管代码块与 git 代理会应用到所有用户发行版；Docker 基础设施发行版会被跳过。
- npm/pip 代理写入用户级配置文件（Windows：`~/.npmrc`、`%APPDATA%\pip\pip.ini`；WSL：`~/.npmrc`、`~/.config/pip/pip.conf`），不会触碰项目级 `.npmrc`。当开关显示为「已启用」（即文件中存在代理值）时，关闭会移除该值——包括用户先前手动写入的值，因为工具无法区分二者。
- WSL 侧 npm/pip 端点经 `wsl.exe` 读写配置文件，每次读取/写入都是一次 wsl.exe 往返，因此 WSL 开关的状态刷新比 Windows 侧慢。
- 「重启 WSL」会终止 WSL 内正在运行的所有进程。

## 项目结构

```text
ProxyManager/
├── app.py                     # 前端入口：pywebview 窗口、JS 桥、托盘、Win32 窗口逻辑
├── proxy_manager.py           # 后端库：注册表 / WSL / git / npm / pip 操作、遥测、崩溃日志
├── ui/
│   └── index.html             # 零构建单页界面（HTML/CSS/JS）
├── assets/
│   ├── icon.ico               # 应用 / EXE 图标
│   └── tray.png               # 托盘图标
├── build.bat                  # 本地 PyInstaller 打包脚本
├── start.bat                  # 启动器：查找 Python 3.13 并运行 app.py
├── requirements.txt           # 运行与构建依赖
└── .github/
    └── workflows/
        └── release.yml        # Build & Release：v* tag 触发构建并发布 GitHub Release
```

## 开发

源码运行：`py -3.13 app.py`。两个环境变量可辅助调试：

- `PM_DEBUG=1` — 输出详细日志、UI 线程心跳与窗口几何探测信息。
- `PM_NO_SINGLETON=1` — 跳过单实例守卫，便于调试实例与正式实例并行运行。

界面通过 pywebview 的 `js_api` 桥调用 Python（`app.py` 中的 `Api` 类）；所有涉及系统的操作都收敛在 `proxy_manager.py` 中。

## 构建分发

运行 `build.bat`，脚本会安装构建依赖并用 PyInstaller 打包单文件 EXE：

```bat
build.bat
```

产物为 `dist\ProxyManager.exe`。脚本通过 `--hidden-import` 带上延迟导入的托盘相关模块，并把 `ui/` 与 `assets/` 一并打入可执行文件。

CI 执行相同的构建：推送 `v*` tag 会触发 `.github/workflows/release.yml`，在 Windows 上使用 Python 3.13 构建 EXE，上传 artifact 并发布到 GitHub Release。

## 参与贡献

欢迎在 [GitHub 仓库](https://github.com/quji762/ProxyManager) 提交 Issue 与 Pull Request。请保持现有分层：界面改动放 `ui/index.html`，系统操作放 `proxy_manager.py`。

## 许可证

本项目基于 MIT 许可证开源，详见 [LICENSE](./LICENSE)。
