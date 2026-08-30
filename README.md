# ProxyManager

Windows 桌面工具，一键式管理 PowerShell、WSL 与 Git 的代理：四个端点独立开关、端口快选、实时状态，Win11 风格界面。

## 功能

- **四端点独立开关** — PowerShell（用户环境变量）、WSL 终端（`~/.bashrc` 受管代码块）、Git·Windows 与 Git·WSL（两端 `git config --global`）各自独立启停，关闭即清理
- **状态即真相** — 开关自动同步实际检测到的代理状态，操作失败自动弹回
- **端口快选** — 预设端口一键切换（7890 / 7892 / 7897，可自定义管理）；切换时已启用端点自动以新端口重新应用
- **NAT 自适应** — NAT 模式下自动将 `127.0.0.1` 替换为宿主网关 IP（镜像模式保持不变）
- **回环直连** — 自动写入 `NO_PROXY`，本地回环流量不走代理
- **实时遥测** — 实时网速、代理端口连通性每秒探测、本机/WSL IP、WSL 网络模式、两端代理变量（`psutil` 可选）
- **界面** — Win11 风格无边框窗口（边缘原生缩放、双击标题栏最大化）、亮色/暗色主题、自定义背景图与透明度、窗口置顶、关闭到系统托盘、开机自启
- **其他** — 配置持久化、单实例守卫、崩溃日志、应用内确认对话框

## 环境要求

- Windows 10/11（管理 WSL 需已安装 WSL）
- Python 3.9+ 与 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Win11 自带）

## 运行

双击 **`start.bat`**，或：

```bat
pip install -r requirements.txt
py -3.13 app.py
```

## 打包

运行 `build.bat`，产物在 `dist\ProxyManager.exe`。

## 使用

1. 填写代理地址与端口（默认 `127.0.0.1:7897`），或点击端口预设快速选择
2. 打开对应端点的开关；「全部启用 / 全部关闭」可一键操作全部端点
3. 切换主题、选择背景图、开机自启在标题栏「设置」中

## License

MIT
