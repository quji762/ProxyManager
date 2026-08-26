# Proxy Manager

一键管理 Windows PowerShell 与 WSL 的代理环境变量设置。

## 功能

- **一键启用/关闭代理** — 同时设置 PowerShell 注册表环境变量和 WSL bashrc
- **代理状态检测** — 实时显示当前代理是否已启用
- **代理变量查看** — 展示 PowerShell 和 WSL 中 HTTP_PROXY / HTTPS_PROXY 的当前值
- **网络信息** — 显示本机 IP、WSL IP、WSL 网络模式（NAT / Mirrored）
- **点击复制 IP** — 点击「本机 IP / WSL IP」行即可复制到剪贴板
- **窗口置顶** — 标题栏 📌 按钮切换置顶，状态自动保存
- **实时网速** — 显示每秒上传/下载速率（需安装可选依赖 `psutil`，未安装时自动隐藏）
- **亮色 / 暗色主题** — 标题栏 ☀️/🌙 按钮一键切换，状态自动保存
- **系统托盘** — 点击关闭按钮自动隐藏到托盘；右键托盘图标可「显示主窗口」或「退出」
- **WSL 一键重启** — 注入代理后可直接重启 WSL 使其生效
- **配置持久化** — 代理地址、端口、目标选择、置顶状态、主题自动保存

## 截图

<!-- TODO: 添加截图 -->

## 环境要求

- Windows 10/11
- Python 3.9+
- WSL 2（如需管理 WSL 代理）

## 安装

```bash
pip install -r requirements.txt
```

> `psutil`（实时网速卡片）为可选依赖，未安装时程序正常运行，仅自动隐藏网速卡片。

## 界面

采用横长布局（宽 > 高），双栏排列：左侧为状态与网络信息，右侧为代理变量与配置。无滚动条，窗口高度自适应内容。

## 使用

```bash
python proxy_manager.py
```

### 操作步骤

1. 填写代理地址和端口（默认 `127.0.0.1:7897`）
2. 勾选需要注入的目标（PowerShell / WSL）
3. 点击 **启用代理**
4. 如需 WSL 生效，点击 **重启 WSL**

### 说明

- **PowerShell**：通过注册表 `HKCU\Environment` 设置 `HTTP_PROXY` 和 `HTTPS_PROXY`，新开的终端自动生效
- **WSL**：在 `~/.bashrc` 中注入带标记的 export 块（`proxy-manager begin/end`），关闭代理时自动清除
- **WSL 网络模式**：支持 NAT 和 Mirrored 两种模式，Mirrored 下 WSL 直接使用 `127.0.0.1` 访问 Windows 代理

## 打包为 exe

```bash
build.bat
```

输出位于 `dist/ProxyManager.exe`。

## 项目结构

```
ProxyManager/
├── proxy_manager.py    # 主程序
├── requirements.txt    # Python 依赖
├── build.bat           # PyInstaller 打包脚本
├── .gitignore
└── README.md
```

## License

MIT
