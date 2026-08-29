# Proxy Manager

Windows 桌面工具，一键式开关 PowerShell 与 WSL 的代理（含各端 git 全局代理配置）。

## 功能

- **独立开关** — PowerShell 开关管理用户环境变量与 Windows 端 git 代理；WSL 开关管理 `~/.bashrc` 代理块与 WSL 端 git 代理，关闭即清理
- **状态即真相** — 开关自动同步实际检测到的代理状态，操作失败自动弹回
- **NAT 自适应** — NAT 模式下自动将 `127.0.0.1` 替换为宿主网关 IP（镜像模式保持不变）
- **回环直连** — 自动写入 `NO_PROXY`，本地回环流量不走代理
- **代理变量查看** — 展示两端 HTTP_PROXY / HTTPS_PROXY / Git 代理的当前值
- **网络信息** — 本机 IP、WSL IP、WSL 网络模式、实时网速（`psutil` 可选）
- **其他** — 亮色/暗色主题、窗口置顶、关闭到系统托盘、配置持久化

## 环境要求

- Windows 10/11，Python 3.9+（管理 WSL 需已安装 WSL）

## 安装与运行

从 [Releases](https://github.com/quji762/ProxyManager/releases) 下载最新版本，解压后直接运行。

## 使用

1. 填写代理地址与端口（默认 `127.0.0.1:7897`）
2. 打开对应终端的开关，新开的终端即生效；WSL 已开的 shell 可点「重启 WSL」

## License

MIT
