# Proxy Manager

一键管理 Windows PowerShell 与 WSL 的代理环境变量设置。

## 功能

- **一键启用/关闭代理** — 同时设置 PowerShell 注册表环境变量和 WSL bashrc
- **代理状态检测** — 实时显示当前代理是否已启用
- **代理变量查看** — 展示 PowerShell 和 WSL 中 HTTP_PROXY / HTTPS_PROXY 的当前值
- **网络信息** — 显示本机 IP、WSL IP、WSL 网络模式（NAT / Mirrored）
- **WSL 一键重启** — 注入代理后可直接重启 WSL 使其生效
- **配置持久化** — 代理地址、端口、目标选择自动保存

## 截图

<!-- TODO: 添加截图 -->

## 环境要求

- Windows 10/11
- Python 3.9+
- WSL 2（如需管理 WSL 代理）

## 安装

```bash
pip install customtkinter
```

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
